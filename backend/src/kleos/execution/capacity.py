"""Durable aggregate compute admission; never a workflow scheduler or expiring lease."""
import re
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass

from sqlalchemy import JSON, Float, String, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, mapped_column

from kleos.contracts.models import DomainError
from kleos.execution.protocol import fingerprint
from kleos.projects.store import Base


@dataclass(frozen=True)
class CapacityLimits:
    cpus: int = 4
    memory_bytes: int = 6 * 1024**3
    scratch_bytes: int = 5 * 1024**3
    max_jobs: int = 2

    def __post_init__(self):
        values = asdict(self)
        if (any(type(value) is not int for value in values.values()) or not 1 <= self.cpus <= 256
                or not 512 * 1024**2 <= self.memory_bytes <= 1024**4
                or not 128 * 1024**2 <= self.scratch_bytes <= 1024**4 or not 1 <= self.max_jobs <= 128):
            raise ValueError('Aggregate native capacity limits are invalid')

    def wire(self):
        return {'cpus': self.cpus, 'memoryBytes': self.memory_bytes,
                'scratchBytes': self.scratch_bytes, 'maxJobs': self.max_jobs}


class CapacityPool(Base):
    __tablename__ = 'kleos2_capacity_pools'
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    limits: Mapped[dict] = mapped_column(JSON)


class CapacityReservation(Base):
    __tablename__ = 'kleos2_capacity_reservations'
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    pool_id: Mapped[str] = mapped_column(String(100), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    resources: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), index=True)
    reserved_at: Mapped[float] = mapped_column(Float)
    released_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    proof: Mapped[dict] = mapped_column(JSON)
    details: Mapped[dict] = mapped_column(JSON)


def resource_contract(value):
    if (not isinstance(value, dict) or not {'cpus', 'memoryBytes', 'scratchBytes'} <= set(value)
            or set(value) - {'cpus', 'memoryBytes', 'scratchBytes', 'pids'}
            or any(type(number) is not int or number < 1 for number in value.values())):
        raise DomainError('Native capacity requires an exact positive resource contract', 'invalid_resources')
    return dict(value)


class CapacityService:
    """Server-only admission shared by every adapter targeting one compute pool.

    Pool settings are persistent and immutable: differently configured processes
    fail closed. No deadline/heartbeat automatically releases uncertain compute.
    Callers may pass their transaction to atomically persist dispatch identity and
    admission. They must not hold a transaction across Docker/network IO.
    """
    def __init__(self, projects, limits=None, *, pool_id='local-docker', clock=time.time):
        if not isinstance(pool_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', pool_id):
            raise ValueError('Native capacity pool ID is invalid')
        self.projects, self.limits, self.pool_id = projects, limits or CapacityLimits(), pool_id
        self.clock = clock

    def _lock(self, session):
        dialect = session.get_bind().dialect.name
        if dialect == 'postgresql':
            # Serializes first creation as well as subsequent cross-process checks.
            lock_id = int(fingerprint(['kleos-native-capacity-v1', self.pool_id])[:15], 16)
            if not session.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': lock_id}):
                return False  # Never block the worker heartbeat on another admission transaction.
            pool = session.get(CapacityPool, self.pool_id)
            if pool is None:
                pool = CapacityPool(id=self.pool_id, limits=self.limits.wire())
                session.add(pool)
                session.flush()
        elif dialect == 'sqlite':
            # Obtain the database write lock before reading usage (fixtures only).
            session.execute(sqlite_insert(CapacityPool).values(id=self.pool_id, limits=self.limits.wire())
                            .on_conflict_do_nothing(index_elements=['id']))
            pool = session.get(CapacityPool, self.pool_id)
        else:
            raise DomainError('Native capacity requires PostgreSQL persistence', 'capacity_persistence_unavailable', 503)
        if pool.limits != self.limits.wire():
            raise DomainError('Native capacity pool configuration differs across workers', 'capacity_configuration_changed', 503)
        return True

    def _key(self, key, request_hash):
        if (not isinstance(key, str) or not re.fullmatch(r'(native|slicer|checker|mcp):[A-Za-z0-9_-]{1,128}', key)
                or not isinstance(request_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', request_hash)):
            raise DomainError('Invalid native capacity reservation identity', 'capacity_identity_conflict')

    def _rollout_guard(self, session, key):
        # Known pre-admission work must be reconciled, never assigned guessed defaults.
        # Imports stay local because the adapters themselves depend on this service.
        from kleos.capabilities.fabrication.slicing import PrintPreparation
        from kleos.execution.gateway import NativeAction, NativeJob

        held = set(session.scalars(select(CapacityReservation.key).where(CapacityReservation.status == 'reserved')))
        for job in session.scalars(select(NativeJob)):
            if 'native:' + job.id in held:
                continue
            cleaned = session.get(NativeAction, (job.id, '_cleanup'))
            if (cleaned is not None and cleaned.status == 'completed' and cleaned.request_hash == job.request_hash
                    and cleaned.result == {'kind': 'cleanup-proof', 'jobId': job.id, 'containerRemoved': True}):
                continue
            if job.request.get('capacityPoolId') == self.pool_id and job.status == 'pending' and job.active_action is None:
                continue  # A new pinned job waiting for admission has not dispatched.
            raise DomainError('Reconcile existing native compute before new admission', 'native_capacity_rollout_blocked', 409)
        for ident in session.scalars(select(PrintPreparation.container_id).where(PrintPreparation.container_id.is_not(None))):
            if 'slicer:' + ident not in held and 'slicer:' + ident != key:
                raise DomainError('Reconcile existing slicer compute before new admission', 'native_capacity_rollout_blocked', 409)

    def reserve(self, key, project_id, request_hash, resources, *, session=None, details=None):
        self._key(key, request_hash)
        resources = resource_contract(resources)
        if not isinstance(project_id, str) or not 1 <= len(project_id) <= 36:
            raise DomainError('Native capacity requires a bound project', 'capacity_identity_conflict')
        details = details or {}
        if (not isinstance(details, dict) or set(details) - {'containerName', 'imageDigest', 'protocol'}
                or any(not isinstance(value, str) or not 1 <= len(value) <= 200 for value in details.values())):
            raise DomainError('Invalid trusted native capacity metadata', 'capacity_identity_conflict')
        with nullcontext(session) if session is not None else self.projects.transaction() as transaction:
            if not self._lock(transaction):
                return False
            old = transaction.get(CapacityReservation, key)
            if old is not None:
                if (old.pool_id != self.pool_id or old.project_id != project_id or old.request_hash != request_hash
                        or old.resources != resources or old.details != details):
                    raise DomainError('Native capacity identity was reused with another contract', 'capacity_identity_conflict')
                if old.status != 'reserved':
                    raise DomainError('Native capacity reservation is already closed', 'capacity_reservation_closed')
                return True
            self._rollout_guard(transaction, key)
            limits = self.limits.wire()
            if any(resources[name] > limits[name] for name in ('cpus', 'memoryBytes', 'scratchBytes')):
                raise DomainError('Native job exceeds the configured aggregate capacity', 'native_capacity_exceeded', 409)
            active = list(transaction.scalars(select(CapacityReservation).where(
                CapacityReservation.pool_id == self.pool_id, CapacityReservation.status == 'reserved')))
            if len(active) >= limits['maxJobs'] or any(
                    sum(row.resources[name] for row in active) + resources[name] > limits[name]
                    for name in ('cpus', 'memoryBytes', 'scratchBytes')):
                return False
            transaction.add(CapacityReservation(key=key, pool_id=self.pool_id, project_id=project_id,
                request_hash=request_hash, resources=resources, status='reserved', reserved_at=self.clock(),
                released_at=None, proof={}, details=dict(details)))
            transaction.flush()
            return True

    def release(self, key, request_hash, proof, *, session=None):
        self._key(key, request_hash)
        if (not isinstance(proof, dict) or set(proof) != {'kind', 'identity'}
                or proof['kind'] != 'container-removed' or not isinstance(proof['identity'], str)
                or not 1 <= len(proof['identity']) <= 200):
            raise DomainError('Native capacity needs verified container-removal evidence', 'capacity_termination_unconfirmed')
        with nullcontext(session) if session is not None else self.projects.transaction() as transaction:
            if not self._lock(transaction):
                raise DomainError('Native capacity settlement is busy; retain the reservation', 'native_capacity_busy', 409)
            row = transaction.get(CapacityReservation, key)
            if row is None:
                return False  # Legacy cleanup creates no false reservation/release history.
            if row.pool_id != self.pool_id or row.request_hash != request_hash:
                raise DomainError('Native capacity release identity changed', 'capacity_identity_conflict')
            if row.details.get('containerName') and proof['identity'] != row.details['containerName']:
                raise DomainError('Native capacity removal proof names another container', 'capacity_identity_conflict')
            if row.status == 'released':
                if row.proof != proof:
                    raise DomainError('Native capacity release proof changed', 'capacity_identity_conflict')
                return True
            if row.status != 'reserved':
                raise DomainError('Native capacity reservation state is invalid', 'capacity_identity_conflict')
            row.status, row.released_at, row.proof = 'released', self.clock(), dict(proof)
            return True

    def usage(self):
        with self.projects.transaction() as session:
            if not self._lock(session):
                raise DomainError('Native capacity ledger is busy', 'native_capacity_busy', 409)
            rows = list(session.scalars(select(CapacityReservation).where(
                CapacityReservation.pool_id == self.pool_id, CapacityReservation.status == 'reserved')))
            return {'poolId': self.pool_id, 'limits': self.limits.wire(), 'jobs': len(rows),
                    **{name: sum(row.resources[name] for row in rows)
                       for name in ('cpus', 'memoryBytes', 'scratchBytes')}}
