"""检查证据与用户操作分别保留，旧依据不会冒充当前结论。"""
from __future__ import annotations

import copy
import uuid


def review_items(doc: dict, *, include_all: bool = False) -> list[dict]:
    items = []
    for check in doc['checks']:
        if check['outdated'] and not include_all:
            continue
        if check['status'] != 'checked':
            items.append({'item_id': check['check_id'], 'check_id': check['check_id'],
                          'indices': check['indices'], 'issues': [], 'state': 'check_pending',
                          'outdated': check['outdated'], 'context': check['entries'],
                          'check_status': check['status'], 'can_accept': False})
        for item in check['items']:
            if include_all or item['state'] == 'pending':
                items.append({**item, 'check_id': check['check_id'], 'outdated': check['outdated'],
                              'context': check['entries'], 'check_status': check['status'], 'can_accept': not check['outdated']})
    return items


def make_check(doc: dict, indices: list[int], issues: list[dict], status: str, details: dict) -> dict:
    groups: dict[tuple[int, ...], list[dict]] = {}
    allowed = set(indices)
    for issue in issues:
        scope = set(issue.get('indices', [])) & allowed
        if not scope:
            continue
        values = [copy.deepcopy(issue)]
        # 只合并共享实际字幕范围的证据；单纯时间相邻不会组成同一修复组。
        overlapping = [key for key in groups if scope.intersection(key)]
        for key in overlapping:
            scope.update(key)
            values.extend(groups.pop(key))
        groups[tuple(sorted(scope))] = values
    return {
        'check_id': str(uuid.uuid4()), 'indices': list(indices), 'status': status,
        'revision': doc['revision'], 'outdated': False, 'details': details,
        'entries': [copy.deepcopy(e) for e in doc['entries'] if e['index'] in allowed],
        'items': [{'item_id': str(uuid.uuid4()), 'indices': list(scope), 'issues': values, 'state': 'pending'}
                  for scope, values in groups.items()],
    }


def invalidate_checks(doc: dict) -> None:
    # 完整上下文可能跨越相邻片段，内容变化后保守失效所有当前语义判断。
    current = [check for check in doc['checks'] if not check['outdated']]
    for check in current:
        check['outdated'] = True
    scopes = {tuple(check['indices']) for check in current}
    for scope in scopes:
        doc['checks'].append(make_check(doc, list(scope), [], 'pending', {'reason': 'content_changed'}))
