"""A transaction observed before acquiring the lock may end before the append."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

import psycopg
from psycopg.pq import TransactionStatus

from geniusnew.audit import AuditAuthority
from geniusnew.audit_store import PostgresAuditChain
from geniusnew.contracts import ContractError
from tests import test_audit_store


class ExternalAuditTransactionTest(unittest.TestCase):
    def test_a_waiting_thread_rechecks_the_external_transaction_under_the_lock(self):
        connection = Mock(spec=psycopg.Connection)
        connection.autocommit = True
        connection.info = SimpleNamespace(transaction_status=TransactionStatus.INTRANS)
        authority = AuditAuthority(audit_key=b"test-only-key" * 4)
        with patch.object(PostgresAuditChain, "_snapshot", return_value=(None, ())):
            chain = PostgresAuditChain(connection, authority=authority)
        fixture = test_audit_store.StoredAuditSnapshotTest()
        fixture.setUp()
        main_thread = threading.get_ident()
        waiting = threading.Event()
        original_lock = chain._lock

        class ObservedLock:
            def __enter__(self):
                if threading.get_ident() != main_thread:
                    waiting.set()
                return original_lock.__enter__()

            def __exit__(self, *exc):
                return original_lock.__exit__(*exc)

        chain._lock = ObservedLock()
        with patch.object(chain, "_append", return_value=object()) as append:
            with ThreadPoolExecutor(max_workers=1) as executor:
                with chain._lock:
                    future = executor.submit(chain.append, fixture.event,
                                             transaction=connection)
                    self.assertTrue(waiting.wait(3))
                    connection.info.transaction_status = TransactionStatus.IDLE
                with self.assertRaisesRegex(ContractError, "active external transaction"):
                    future.result(timeout=3)
            append.assert_not_called()
