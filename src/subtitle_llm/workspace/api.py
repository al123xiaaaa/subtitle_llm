"""结构化本地 API。命令分发不暴露配置凭据，也不把第三方异常原文返回界面。"""
from .budget import BudgetExceeded, BudgetLedger
from .operations import WorkspaceOperations
from .store import WorkspaceConflict, WorkspaceStore
from .versions import fork_version


def dispatch(workspace: WorkspaceStore, request: dict):
    action = request.get('action')
    task_id = request.get('task_id', '')
    if action == 'library':
        workspace.sync_all()
        return workspace.list_materials()
    if action == 'version':
        doc = workspace.sync_task(task_id)
        doc['review_items'] = workspace.review_items(task_id, include_all=True)
        try:
            doc['budget'] = BudgetLedger(workspace.db_path).state(task_id + ':auto')
        except ValueError:
            doc['budget'] = None
        return doc
    if action == 'pin':
        workspace.pin_version(request['material_id'], request['language'], request.get('task_id'))
        return True
    if action == 'accept':
        return workspace.accept_items(task_id, request['item_ids'])
    if action == 'edit':
        return workspace.edit(task_id, request['changes'], expected_revision=request['revision'])
    if action == 'undo':
        return workspace.undo(task_id, request['history_id'], expected_revision=request['revision'], force=request.get('force') is True)
    if action == 'associate':
        return workspace.associate(task_id, request['material_id'])
    if action == 'fork':
        return fork_version(workspace, task_id, model=request.get('model'), rerun=request.get('rerun') is True)
    if action == 'export_subtitle':
        return workspace.export_subtitle(task_id, request['path'], partial=request.get('partial') is True)
    if action == 'export_video':
        from .media import export_video
        return export_video(workspace, task_id, request['path'], video=request.get('video'), partial=request.get('partial') is True)
    operations = WorkspaceOperations(workspace)
    if action == 'estimate':
        return operations.estimate(task_id, request['indices'], repair=request.get('repair') is True)
    if action == 'check':
        return operations.check(task_id, request['indices'], token_limit=request['token_limit'])
    if action == 'repair':
        return operations.repair(task_id, request['item_id'], token_limit=request['token_limit'])
    if action == 'retranscribe':
        from .media import retranscribe
        return retranscribe(operations, task_id, request['indices'], token_limit=request['token_limit'])
    raise ValueError('不支持的素材操作')


def respond(workspace, request):
    try:
        return {'ok': True, 'value': dispatch(workspace, request)}
    except WorkspaceConflict as error:
        return {'ok': False, 'code': 'conflict', 'error': str(error)}
    except BudgetExceeded:
        return {'ok': False, 'code': 'budget', 'error': '本次额度不足以覆盖完整处理与验收，请提高明确额度或保留待复核'}
    except (ValueError, FileNotFoundError, FileExistsError):
        return {'ok': False, 'code': 'invalid', 'error': '操作无法执行：请检查范围、文件、额度或版本是否仍有效；原内容已保留'}
    except Exception:
        return {'ok': False, 'code': 'failed', 'error': '操作未完成，原内容已保留；请检查服务状态后重试'}
