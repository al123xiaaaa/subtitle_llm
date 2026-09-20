"""按处理轮次预留资源，跨进程和恢复共享余额，未知消耗不会被记为免费。"""
from __future__ import annotations

import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class BudgetExceeded(ValueError):
    pass


class BudgetLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        with self._connect() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS workspace_budgets (
                    bucket TEXT PRIMARY KEY, baseline INTEGER NOT NULL, ratio REAL NOT NULL, token_limit INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspace_budget_calls (
                    call_id TEXT PRIMARY KEY, bucket TEXT NOT NULL REFERENCES workspace_budgets(bucket),
                    reserved INTEGER NOT NULL, actual INTEGER, state TEXT NOT NULL DEFAULT 'running'
                );
            ''')

    @contextmanager
    def _connect(self):
        with sqlite3.connect(self.path, timeout=30) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            yield connection

    def configure(self, bucket: str, *, baseline: int = 0, ratio: float = 0.3,
                  limit: int | None = None) -> dict:
        if type(baseline) is not int or baseline < 0 or not math.isfinite(ratio) or not 0 <= ratio <= 1 or (limit is not None and (type(limit) is not int or limit < 0)):
            raise ValueError('预算必须为非负有限值')
        token_limit = math.floor(baseline * ratio) if limit is None else limit
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            previous = connection.execute('SELECT * FROM workspace_budgets WHERE bucket=?', (bucket,)).fetchone()
            # 同一任务续跑可累计新的首轮用量，但不能抹掉已经消耗和预留的额度。
            if previous:
                baseline = max(baseline, previous['baseline'])
                if limit is None:
                    token_limit = math.floor(baseline * ratio)
            connection.execute('''INSERT INTO workspace_budgets VALUES (?, ?, ?, ?)
                ON CONFLICT(bucket) DO UPDATE SET baseline=excluded.baseline, ratio=excluded.ratio,
                token_limit=excluded.token_limit''', (bucket, baseline, ratio, token_limit))
        return self.state(bucket)

    @staticmethod
    def _state(connection, bucket: str) -> dict:
        row = connection.execute('SELECT * FROM workspace_budgets WHERE bucket=?', (bucket,)).fetchone()
        if row is None:
            raise ValueError('预算尚未建立')
        calls = connection.execute('SELECT * FROM workspace_budget_calls WHERE bucket=?', (bucket,)).fetchall()
        spent = sum(call['actual'] or 0 for call in calls)
        reserved = sum(call['reserved'] for call in calls if call['actual'] is None)
        return {'bucket': bucket, 'baseline': row['baseline'], 'ratio': row['ratio'], 'limit': row['token_limit'],
                'spent': spent, 'reserved': reserved, 'available': max(0, row['token_limit'] - spent - reserved),
                'unknown_calls': sum(call['state'] == 'unknown' for call in calls), 'call_count': len(calls)}

    def state(self, bucket: str) -> dict:
        with self._connect() as connection:
            return self._state(connection, bucket)

    def reserve(self, bucket: str, call_id: str, tokens: int) -> bool:
        if type(tokens) is not int or tokens <= 0:
            raise ValueError('预留用量必须为正整数')
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            old = connection.execute('SELECT bucket FROM workspace_budget_calls WHERE call_id=?', (call_id,)).fetchone()
            if old:
                if old['bucket'] != bucket:
                    raise ValueError('同一处理标识不能跨预算复用')
                return False
            if self._state(connection, bucket)['available'] < tokens:
                raise BudgetExceeded('剩余额度不足以覆盖本轮处理及验收，已保留当前译文和疑点')
            connection.execute('INSERT INTO workspace_budget_calls (call_id,bucket,reserved) VALUES (?, ?, ?)',
                               (call_id, bucket, tokens))
            return True

    def settle(self, call_id: str, actual: int | None) -> None:
        if actual is not None and (type(actual) is not int or actual < 0):
            raise ValueError('实际用量必须是非负整数或未知')
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            old = connection.execute('SELECT * FROM workspace_budget_calls WHERE call_id=?', (call_id,)).fetchone()
            if old is None:
                raise ValueError('该调用未预留预算')
            if old['actual'] is not None:
                if old['actual'] != actual:
                    raise ValueError('已结算调用不能重复记为另一份用量')
                return
            connection.execute('UPDATE workspace_budget_calls SET actual=?,state=? WHERE call_id=?',
                               (actual, 'unknown' if actual is None else 'settled', call_id))
