"""Standalone bounded data readers. Never import or execute candidate application/firmware code."""
import hashlib
import io
import json
import os
import re
import socket
import stat
import struct
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

PROTOCOL = 'kleos-export-check-v1'
MAX_FILE = 64*1024**2
MAX_TOTAL = 128*1024**2
OUTPUTS = {'firmware': {'outputs/firmware.elf', 'outputs/firmware.bin'},
           'application': {'outputs/companion-web.zip'}}
BOARD = 'nrf52840dk/nrf52840'


class InvalidExport(ValueError):
    pass


class UnsupportedExport(ValueError):
    pass


def safe_path(value):
    if (not isinstance(value, str) or not value or len(value) > 240 or '\\' in value
            or any(ord(char) < 32 for char in value) or PurePosixPath(value).is_absolute()
            or any(part in {'', '.', '..'} for part in value.split('/'))):
        raise InvalidExport('Unsafe export path')
    return value


def json_data(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise InvalidExport('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def firmware_measurements(elf, binary, target):
    """ELF32 ARM load addresses and independently reconstructed objcopy-style binary.

    Supports allocated PROGBITS and ARM EXIDX data within PT_LOAD segments;
    unknown allocated section types or unusual layouts remain UNKNOWN.
    """
    if target != {'board': BOARD}:
        raise UnsupportedExport('Firmware board is outside the checked contract')
    if len(elf) < 52 or elf[:7] != b'\x7fELF\x01\x01\x01':
        raise InvalidExport('Expected ELF32 little-endian executable')
    fields = struct.unpack_from('<HHIIIIIHHHHHH', elf, 16)
    kind, machine, version, entry, phoff, shoff, _, ehsize, phsize, phnum, shsize, shnum, _ = fields
    if (kind != 2 or machine != 40 or version != 1 or ehsize != 52 or phsize != 32
            or shsize != 40 or not 1 <= phnum <= 128 or not 1 <= shnum <= 4096
            or phoff < 52 or shoff < 52 or phoff+phnum*32 > len(elf) or shoff+shnum*40 > len(elf)):
        raise InvalidExport('Invalid ARM executable headers')
    loads = []
    for index in range(phnum):
        typ, off, va, pa, size, mem, flags, align = struct.unpack_from('<8I', elf, phoff+index*32)
        if typ != 1:
            continue
        if (size > mem or off+size > len(elf) or va+mem > 2**32 or pa+size > 2**32
                or align and (align & (align-1))):
            raise InvalidExport('Invalid firmware load segment')
        if mem and not (0 <= va < va+mem <= 1024**2 or 0x20000000 <= va < va+mem <= 0x20040000):
            raise InvalidExport('Firmware exceeds board memory')
        if size and not 0 <= pa < pa+size <= 1024**2:
            raise InvalidExport('Firmware load image exceeds flash')
        loads.append((off, va, pa, size, mem, flags))
    if not loads or not any(flags & 1 and va <= entry & ~1 < va+mem for _, va, _, _, mem, flags in loads):
        raise InvalidExport('Firmware entry is outside executable memory')
    spans = []
    for index in range(shnum):
        _, typ, flags, va, off, size, _, _, _, _ = struct.unpack_from('<10I', elf, shoff+index*40)
        if not flags & 2 or not size:
            continue
        if typ == 8:  # NOBITS contributes memory only, never bytes to .bin.
            if not any(base <= va < va+size <= base+mem for _, base, _, _, mem, _ in loads):
                raise InvalidExport('Firmware uninitialized section exceeds its load region')
            continue
        if typ == 0x70000001:  # SHT_ARM_EXIDX is file-backed pairs of 32-bit words.
            if size % 8 or va % 4 or not flags & 0x80:
                raise InvalidExport('Malformed ARM exception-index section layout')
        elif typ != 1:
            raise UnsupportedExport('Allocated ELF section type is not qualified')
        if off+size > len(elf):
            raise InvalidExport('Firmware section bytes exceed file')
        matches = [(pa+va-base, off, size) for seg_off, base, pa, length, _, _ in loads
                   if base <= va < va+size <= base+length and seg_off+va-base == off]
        if len(matches) != 1:
            raise UnsupportedExport('Firmware section mapping is ambiguous')
        spans.extend(matches)
    spans.sort()
    if not spans or spans[0][0] != 0 or any(a+size > b for (a, _, size), (b, _, _) in zip(spans, spans[1:])):
        raise UnsupportedExport('Firmware requires nonoverlapping flash sections beginning at zero')
    length = spans[-1][0]+spans[-1][2]
    if length > 1024**2 or len(binary) != length or len(binary) < 8:
        raise InvalidExport('Firmware binary size differs from ELF load image')
    rebuilt = bytearray(b'\xff'*length)  # Pinned Zephyr BIN contract uses erased-flash gap fill.
    for address, off, size in spans:
        rebuilt[address:address+size] = elf[off:off+size]
    if bytes(rebuilt) != binary:
        raise InvalidExport('Firmware binary differs from ELF allocated bytes')
    stack, reset = struct.unpack_from('<II', binary)
    if not 0x20000000 < stack <= 0x20040000 or stack % 8 or reset != entry or not reset & 1:
        raise InvalidExport('Firmware vectors do not match board memory and entry')
    return {'board': BOARD, 'elfMachine': 'ARM', 'entryAddress': entry, 'flashBytes': length,
            'loadSegments': len(loads), 'binaryMatchesElf': True, 'binaryGapFill': 255, 'firmwareExecuted': False}


class ResourceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.resources = []

    def handle_starttag(self, tag, attrs):
        if len({name for name, _ in attrs}) != len(attrs):
            raise InvalidExport('Duplicate HTML attributes are ambiguous')
        attrs = dict(attrs)
        if tag == 'base' and attrs.get('href') != '/':
            raise UnsupportedExport('Only root-relative Flutter base is supported')
        if tag in {'iframe', 'object', 'embed'}:
            raise UnsupportedExport('Embedded documents require separate checks')
        for field in (('src',) if tag in {'script', 'img', 'source', 'video', 'audio'} else ('href',) if tag == 'link' else ()):
            if field in attrs:
                self.resources.append(attrs[field])


def archive_identities(raw):
    """Pre-bound central directory and raw local names before ZipInfo normalization.

    This packaging scope accepts ordinary single-disk non-ZIP64 archives without
    data descriptors or alternate Unicode-name extra fields. No extraction occurs.
    """
    end = raw.rfind(b'PK\x05\x06', max(0, len(raw)-65557))
    if end < 0 or end+22 > len(raw):
        raise InvalidExport('Missing bounded ZIP directory')
    _, disk, start_disk, disk_count, count, size, offset, comment = struct.unpack_from('<4s4H2IH', raw, end)
    if (disk or start_disk or disk_count != count or not 1 <= count <= 4096
            or end+22+comment != len(raw) or offset+size != end or size > 2*1024**2):
        raise InvalidExport('Unsupported ZIP directory bounds')
    position, identities, ranges, prefix_names, kinds = offset, [], [], {}, {}
    def check_extras(extra):
        index = 0
        while index < len(extra):
            if index+4 > len(extra):
                raise InvalidExport('Truncated ZIP extra field')
            key, length = struct.unpack_from('<HH', extra, index)
            index += 4
            if index+length > len(extra) or key in {1, 0x7075, 0x6375}:
                raise InvalidExport('Unsupported alternate ZIP identity')
            index += length
    for _ in range(count):
        if position+46 > end or raw[position:position+4] != b'PK\x01\x02':
            raise InvalidExport('Invalid ZIP central entry')
        fields = struct.unpack_from('<4s6H3I5H2I', raw, position)
        flags, method, crc, packed, length = fields[3], fields[4], fields[7], fields[8], fields[9]
        name_len, extra_len, comment_len, member_disk, local = fields[10], fields[11], fields[12], fields[13], fields[16]
        next_position = position+46+name_len+extra_len+comment_len
        if (flags & ~0x800 or method not in {0, 8} or member_disk or not 1 <= name_len <= 240
                or next_position > end or local+30 > offset or max(packed, length, local) == 0xffffffff):
            raise InvalidExport('Unsupported ZIP member encoding or bounds')
        encoded = raw[position+46:position+46+name_len]
        try:
            name = encoded.decode('utf-8' if flags & 0x800 else 'cp437')
        except UnicodeDecodeError as error:
            raise InvalidExport('Invalid ZIP filename encoding') from error
        directory = name.endswith('/')
        path = safe_path(name[:-1] if directory else name)
        check_extras(raw[position+46+name_len:position+46+name_len+extra_len])
        if raw[local:local+4] != b'PK\x03\x04':
            raise InvalidExport('Missing ZIP local header')
        header = struct.unpack_from('<4s5H3I2H', raw, local)
        local_name, local_extra = header[9], header[10]
        data_start = local+30+local_name+local_extra
        if (header[2] != flags or header[3] != method or header[6:9] != (crc, packed, length)
                or raw[local+30:local+30+local_name] != encoded or data_start+packed > offset):
            raise InvalidExport('ZIP local and central identities differ')
        check_extras(raw[local+30+local_name:data_start])
        ranges.append((local, data_start+packed))
        parts = path.split('/')
        for depth in range(1, len(parts)+1):
            prefix = '/'.join(parts[:depth])
            key = prefix.casefold()
            if key in prefix_names and prefix_names[key] != prefix:
                raise InvalidExport('Case-ambiguous ZIP directory prefix')
            prefix_names[key] = prefix
            is_directory = depth < len(parts) or directory
            if key in kinds and kinds[key] != is_directory:
                raise InvalidExport('ZIP file and directory collide')
            kinds[key] = is_directory
        identities.append(name)
        position = next_position
    ranges.sort()
    if (position != end or len(set(identities)) != len(identities) or ranges[0][0] != 0
            or any(previous[1] > following[0] for previous, following in zip(ranges, ranges[1:]))):
        raise InvalidExport('Ambiguous or overlapping ZIP members')
    return identities


def web_measurements(raw):
    if len(raw) > MAX_FILE:
        raise InvalidExport('Web archive exceeds byte limit')
    identities = archive_identities(raw)
    files, total = {}, 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if not 1 <= len(entries) <= 4096:
                raise InvalidExport('Web archive count exceeds limit')
            seen = set()
            if [info.orig_filename for info in entries] != identities or any(info.filename != info.orig_filename for info in entries):
                raise InvalidExport('ZIP decoded member identity changed')
            for info in entries:
                name = safe_path(info.filename.rstrip('/') if info.is_dir() else info.filename)
                if name.casefold() in seen:
                    raise InvalidExport('Duplicate or case-ambiguous web path')
                seen.add(name.casefold())
                mode = info.external_attr >> 16
                if (info.flag_bits & 1 or info.compress_type not in {0, 8}
                        or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}
                        or info.file_size > 32*1024**2):
                    raise InvalidExport('Unsupported web archive member')
                if info.is_dir():
                    continue
                total += info.file_size
                if total > MAX_TOTAL or (info.file_size > 1024**2 and info.file_size > max(1, info.compress_size)*1000):
                    raise InvalidExport('Web archive expansion exceeds limit')
                with archive.open(info) as stream:
                    data = stream.read(min(info.file_size+1, 32*1024**2+1))
                if len(data) != info.file_size:
                    raise InvalidExport('Web archive size mismatch')
                files[name] = data
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise InvalidExport('Invalid web archive structure or checksum') from error
    required = {'index.html', 'flutter_bootstrap.js', 'main.dart.js', 'assets/FontManifest.json',
                'canvaskit/canvaskit.js', 'canvaskit/canvaskit.wasm'}
    if not required <= files.keys() or any(not files[name] for name in required):
        raise InvalidExport('Web bundle lacks required local Flutter resources')
    if len(files['index.html']) > 256*1024 or len(files['flutter_bootstrap.js']) > 1024**2:
        raise InvalidExport('Web entry files exceed bounds')
    parser = ResourceParser()
    parser.feed(files['index.html'].decode('utf-8'))
    for value in parser.resources:
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc:
            raise UnsupportedExport('Web entry requires an external resource')
        path = safe_path(unquote(parsed.path).removeprefix('/'))
        if path not in files:
            raise InvalidExport('Web entry refers to a missing resource')
    bootstrap = files['flutter_bootstrap.js'].decode('utf-8')
    if 'main.dart.js' not in bootstrap or 'canvaskit' not in bootstrap.lower():
        raise UnsupportedExport('Unsupported Flutter bootstrap layout')
    # This checks the finite packaging contract, not arbitrary JS network behavior.
    if re.search(r'canvasKitBaseUrl\s*:\s*[\"\']https?://', bootstrap):
        raise UnsupportedExport('Flutter renderer is configured remotely')
    if len(files['assets/FontManifest.json']) > 256*1024:
        raise InvalidExport('Font manifest exceeds limit')
    fonts = json_data(files['assets/FontManifest.json'])
    if not isinstance(fonts, list) or not 1 <= len(fonts) <= 256:
        raise InvalidExport('Font manifest is invalid')
    families, count = set(), 0
    for family in fonts:
        if (not isinstance(family, dict) or not isinstance(family.get('family'), str)
                or family['family'] in families or not isinstance(family.get('fonts'), list)
                or not 1 <= len(family['fonts']) <= 32):
            raise InvalidExport('Font family is ambiguous or invalid')
        families.add(family['family'])
        for font in family['fonts']:
            name = 'assets/'+safe_path(font.get('asset') if isinstance(font, dict) else None)
            if name not in files or files[name][:4] not in {b'\x00\x01\x00\x00', b'OTTO', b'wOFF', b'wOF2'}:
                raise InvalidExport('Font asset is missing or has an invalid header')
            count += 1
    if 'Roboto' not in families:
        raise InvalidExport('Default Flutter Roboto font is not bundled')
    return {'files': len(files), 'expandedBytes': total, 'fontAssets': count,
            'requiredLocalResourcesPresent': True, 'browserExecuted': False,
            'networkBehaviorVerified': False, 'applicationSecurityVerified': False}


def inspect_request(request, directory):
    lane = request.get('lane')
    files = request.get('files')
    if (request.get('protocol') != PROTOCOL or lane not in OUTPUTS or not isinstance(files, list)
            or len(files) != len(OUTPUTS[lane])):
        raise InvalidExport('Unsupported export checker request')
    data = {}
    for item in files:
        name = safe_path(item['path'])
        checksum = item['sha256']
        if name not in OUTPUTS[lane] or name in data or not isinstance(checksum, str) or not re.fullmatch('[a-f0-9]{64}', checksum):
            raise InvalidExport('Invalid export binding')
        path = directory/checksum
        if path.is_symlink() or not path.is_file():
            raise InvalidExport('Export bytes are missing')
        with path.open('rb') as stream:
            raw = stream.read(MAX_FILE+1)
        if len(raw) > MAX_FILE or len(raw) != item['size'] or hashlib.sha256(raw).hexdigest() != checksum:
            raise InvalidExport('Export byte binding differs')
        data[name] = raw
    if lane == 'firmware':
        measurements = firmware_measurements(data['outputs/firmware.elf'], data['outputs/firmware.bin'], request.get('firmwareTarget'))
    else:
        measurements = web_measurements(data['outputs/companion-web.zip'])
    return {'status': 'PASS', 'reason': 'Independent digital export integrity only; no device or application behavior acceptance',
            'measurements': {**measurements, 'physicalAcceptance': False, 'sourceBuildReproduced': False}}


def main():
    request = {}
    isolation = {}
    try:
        raw = Path('/input/request.json').read_bytes()
        if len(raw) > 64*1024:
            raise InvalidExport('Request exceeds byte bound')
        request = json_data(raw)
        status = Path('/proc/self/status').read_text()
        if (os.getuid() != 10001 or os.getgid() != 10001 or 'NoNewPrivs:\t1' not in status
                or 'Seccomp:\t2' not in status or Path('/opt/kleos-native').exists()
                or int(next(line.split()[1] for line in status.splitlines() if line.startswith('CapEff:')), 16)):
            raise RuntimeError('Checker isolation mismatch')
        try:
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except PermissionError:
            pass
        else:
            connection.close()
            raise RuntimeError('Internet socket was permitted')
        if any(value for key, value in os.environ.items() if key not in {'PATH', 'LANG', 'LC_CTYPE', 'HOME', 'HOSTNAME'}):
            raise RuntimeError('Unexpected checker environment')
        isolation = {'uid': 10001, 'gid': 10001, 'noNewPrivileges': True, 'seccomp': True,
                     'capabilities': 0, 'internetSocketDenied': True, 'generatorCodeAbsent': True,
                     'credentialEnvironmentEmpty': True}
        result = inspect_request(request, Path('/input'))
    except UnsupportedExport as error:
        result = {'status': 'UNKNOWN', 'reason': str(error), 'measurements': {}}
    except (InvalidExport, ValueError, KeyError, TypeError, struct.error) as error:
        result = {'status': 'FAIL', 'reason': str(error)[:500], 'measurements': {}}
    except Exception:
        result = {'status': 'ERROR', 'reason': 'Independent export check failed', 'measurements': {}}
    result.update(protocol=PROTOCOL, executionId=request.get('executionId'), subjectHash=request.get('subjectHash'),
                  lane=request.get('lane'), isolation=isolation,
                  checkerSourceSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    print(json.dumps(result, allow_nan=False, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    main()
