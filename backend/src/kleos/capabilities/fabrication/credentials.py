"""Encrypted, tenant-bound credential persistence with cross-process pairing fences.

The injected encryption key is deployment-owned, independent of database data and
never a model/agent setting. Fresh service processes can resume only unexpired
pairings. Neither checkpoints nor memory extraction should query this table.
"""
import json
import os
import time
from contextlib import contextmanager
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ValidationError
from sqlalchemy import Float, LargeBinary, String, delete, func, select, text
from sqlalchemy.orm import Mapped, mapped_column

from kleos.contracts.models import DomainError
from kleos.projects.store import Base

from .connections import StoredPairing
from .models import ConnectionState, PairRequest


class PrinterCredential(Base):
    __tablename__ = 'kleos2_printer_credentials'
    tenant_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    generation: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class EncryptedCredentialStore:
    def __init__(self, session_factory, key: bytes, *, clock=time.monotonic, wall_clock=time.time, capacity=16):
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError('Printer credential encryption requires a persistent 32-byte key')
        self.sessions, self.clock, self.wall_clock = session_factory, clock, wall_clock
        self.capacity, self._cipher = capacity, AESGCM(key)

    @contextmanager
    def _transaction(self):
        with self.sessions() as session:
            try:
                dialect = session.get_bind().dialect.name
                if dialect == 'sqlite':
                    session.execute(text('BEGIN IMMEDIATE'))
                elif dialect == 'postgresql':
                    # Serializes the tiny credential table for capacity and epoch checks.
                    session.execute(text('SELECT pg_advisory_xact_lock(71822024)'))
                else:
                    raise ValueError('Printer credential store requires PostgreSQL or SQLite')
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    @staticmethod
    def _aad(key):
        if (not isinstance(key, tuple) or len(key) != 2
                or any(not isinstance(part, str) or not 1 <= len(part) <= 100 for part in key)):
            raise ValueError('Expected a bounded tenant/user identity')
        return json.dumps(['kleos-printer-credential-v1', *key], separators=(',', ':')).encode()

    def _row(self, session, key):
        self._aad(key)
        row = session.get(PrinterCredential, key)
        if row and row.expires_at <= self.wall_clock():
            session.delete(row)
            session.flush()
            return None
        return row

    def get(self, key):
        with self._transaction() as session:
            row = self._row(session, key)
            if row is None or row.ciphertext is None:
                return None
            try:
                nonce, encrypted = row.ciphertext[:12], row.ciphertext[12:]
                payload = json.loads(self._cipher.decrypt(nonce, encrypted, self._aad(key)))
                remaining = min(1800, row.expires_at - self.wall_clock())
                observed_age = max(0, self.wall_clock() - payload['observedAt'])
                return StoredPairing(PairRequest.model_validate(payload['request']), payload['fingerprint'],
                    self.clock() + remaining, self.clock() - observed_age,
                    ConnectionState.model_validate(payload['view']), row.generation)
            except (InvalidTag, ValueError, KeyError, TypeError, ValidationError) as exc:
                # Wrong keys do not erase valid ciphertext; operators can restore the correct key.
                raise DomainError('Stored printer credentials cannot be unlocked; reconnect or restore the key',
                                  'bambu_credential_unavailable', 409) from exc

    def claim(self, key, *, preserve=False, expected_token=None):
        self._aad(key)
        with self._transaction() as session:
            session.execute(delete(PrinterCredential).where(PrinterCredential.expires_at <= self.wall_clock()))
            row = self._row(session, key)
            if expected_token is not None and (row is None or row.generation != expected_token):
                raise DomainError('Printer pairing changed', 'bambu_pairing_changed', 409)
            if preserve and (row is None or row.ciphertext is None):
                raise DomainError('Pair the printer first', 'bambu_pairing_required', 409)
            if row is None:
                count = session.scalar(select(func.count()).select_from(PrinterCredential))
                if count >= self.capacity:
                    raise DomainError('Local pairing capacity reached', 'bambu_capacity', 429)
                row = PrinterCredential(tenant_id=key[0], user_id=key[1])
                session.add(row)
            row.generation = uuid4().hex
            if not preserve:
                row.ciphertext = None
                row.expires_at = self.wall_clock() + 60
            return row.generation

    def current(self, key, token):
        with self._transaction() as session:
            row = self._row(session, key)
            return row is not None and row.generation == token

    def commit(self, key, token, value):
        remaining = value.expires - self.clock()
        if not 0 < remaining <= 1800:
            raise DomainError('Printer pairing expired', 'bambu_pairing_changed', 409)
        request = value.request.model_dump(mode='json', by_alias=True)
        request['accessCode'] = value.request.access_code.get_secret_value()
        payload = json.dumps({'request': request, 'fingerprint': value.fingerprint,
            'observedAt': self.wall_clock() - max(0, self.clock() - value.observed),
            'view': value.view.model_dump(mode='json', by_alias=True)}, separators=(',', ':')).encode()
        nonce = os.urandom(12)
        encrypted = nonce + self._cipher.encrypt(nonce, payload, self._aad(key))
        with self._transaction() as session:
            row = self._row(session, key)
            if row is None or row.generation != token:
                raise DomainError('Printer pairing changed or expired', 'bambu_pairing_changed', 409)
            row.ciphertext = encrypted
            row.expires_at = self.wall_clock() + remaining

    def delete(self, key, *, expected_token=None):
        self._aad(key)
        with self._transaction() as session:
            row = session.get(PrinterCredential, key)
            if expected_token is not None and (row is None or row.generation != expected_token):
                raise DomainError('Printer pairing changed', 'bambu_pairing_changed', 409)
            if row:
                session.delete(row)

    def purge_expired(self):
        with self._transaction() as session:
            session.execute(delete(PrinterCredential).where(PrinterCredential.expires_at <= self.wall_clock()))

    def close(self):
        # Credentials persist encrypted until original expiry, unlike the memory adapter.
        pass
