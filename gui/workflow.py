# -*- coding: utf-8 -*-
"""
主流程定义与状态计算

整个应用围绕一条固定的流程组织：
    ① 数据导入 → ② 数据标注 → ③ 模型训练 → ④ 结果分析 → ⑤ 模型测试

这里只负责「回答问题」：每一步做完了没有、能不能进、为什么不能进、下一步该干什么。
界面（侧边栏、页头、前置条件页）读取这些结果来显示，不再各自去猜。
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

from models.database import db
from core.app_paths import get_runtime_paths

APP_ROOT = Path(__file__).parent.parent
_RESOURCE_ROOT = APP_ROOT

# 步骤在 QStackedWidget 中的索引，也是流程顺序
STEP_IMPORT = 0
STEP_ANNOTATE = 1
STEP_TRAIN = 2
STEP_RESULT = 3
STEP_TEST = 4

# 辅助页面（不属于主流程）
PAGE_SETTINGS = 5
PAGE_ABOUT = 6

# 每一步的文案。desc 是页头那一行说明——一句，不解释概念。
WORKFLOW_STEPS: List[Dict] = [
    {
        'index': STEP_IMPORT,
        'num': '1',
        'name': '数据导入',
        'short': '导入',
        'desc': '导入图片、视频或已有标注。',
    },
    {
        'index': STEP_ANNOTATE,
        'num': '2',
        'name': '数据标注',
        'short': '标注',
        'desc': '在图片上框出目标并指定类别。',
    },
    {
        'index': STEP_TRAIN,
        'num': '3',
        'name': '模型训练',
        'short': '训练',
        'desc': '用标注好的图片训练模型。',
    },
    {
        'index': STEP_RESULT,
        'num': '4',
        'name': '结果分析',
        'short': '结果',
        'desc': '查看曲线与指标，导出模型。',
    },
    {
        'index': STEP_TEST,
        'num': '5',
        'name': '模型测试',
        'short': '测试',
        'desc': '用新的图片或视频检验模型。',
    },
]

STEP_BY_INDEX = {step['index']: step for step in WORKFLOW_STEPS}

# 步骤状态
DONE = 'done'          # 这一步已经有成果
CURRENT = 'current'    # 可以进，但还没做完
READY = 'ready'        # 前置条件满足，可以进
LOCKED = 'locked'      # 前置条件不满足


def get_project_snapshot(project_id: Optional[int]) -> Dict:
    """读取一个项目当前的进度事实（图片数、标注数、类别数、训练结果）。"""
    snapshot = {
        'project_id': project_id,
        'project': None,
        'name': '',
        'task_type': '',
        'image_count': 0,
        'annotated_count': 0,
        'class_count': 0,
        'run_dirs': [],
        'weights': None,
    }

    if not project_id:
        return snapshot

    project = db.get_project(project_id)
    if not project:
        return snapshot

    snapshot['project'] = project
    snapshot['name'] = project.get('name', '')
    snapshot['task_type'] = project.get('type', '') or ''

    raw_classes = project.get('classes') or '[]'
    try:
        classes = json.loads(raw_classes) if isinstance(raw_classes, str) else raw_classes
    except (ValueError, TypeError):
        classes = []
    snapshot['class_count'] = len(classes) if isinstance(classes, list) else 0

    images = db.get_project_images(project_id)
    snapshot['image_count'] = len(images)
    snapshot['annotated_count'] = sum(
        1 for image in images if image.get('status') == 'annotated'
    )

    snapshot['run_dirs'] = find_project_runs(project_id)
    snapshot['weights'] = find_project_weights(project_id)

    return snapshot


def find_project_runs(project_id: Optional[int]) -> List[Path]:
    """找出这个项目的训练输出目录（runs/**/exp_<id>*，含 weights 子目录的才算）。"""
    if not project_id:
        return []

    # 测试可以继续显式替换 APP_ROOT。生产同时读取新 Workspace 与只读旧版
    # 源码 runs，直到用户完成显式迁移；所有新 writer 只写 Workspace。
    if APP_ROOT != _RESOURCE_ROOT:
        runs_roots = [APP_ROOT / "runs"]
    else:
        try:
            workspace_runs = get_runtime_paths().workspace.runs_root
        except RuntimeError:
            workspace_runs = APP_ROOT / "runs"
        runs_roots = [workspace_runs]
        legacy_runs = APP_ROOT / "runs"
        if legacy_runs != workspace_runs:
            runs_roots.append(legacy_runs)

    matches = []
    base_name = f"exp_{project_id}"
    seen: Dict[Path, int] = {}
    for runs_root in runs_roots:
        if not runs_root.exists():
            continue
        for weights_dir in runs_root.glob("**/weights"):
            run_dir = weights_dir.parent
            if not (
                run_dir.name == base_name or run_dir.name.startswith(base_name + "_")
            ):
                continue
            relative_run = run_dir.relative_to(runs_root)
            existing_index = seen.get(relative_run)
            if existing_index is None:
                seen[relative_run] = len(matches)
                matches.append(run_dir)
                continue
            existing = matches[existing_index]
            existing_best = existing / "weights" / "best.pt"
            candidate_best = run_dir / "weights" / "best.pt"
            if (
                (not existing_best.is_file() or existing_best.is_symlink())
                and candidate_best.is_file()
                and not candidate_best.is_symlink()
            ):
                matches[existing_index] = run_dir
    return sorted(matches, key=_run_recency_key)


def find_project_weights(project_id: Optional[int]) -> Optional[Path]:
    """找出这个项目最新一次训练产出的 best.pt。"""
    for run_dir in reversed(find_project_runs(project_id)):
        best = run_dir / "weights" / "best.pt"
        if best.is_file() and not best.is_symlink():
            return best
    return None


def _run_recency_key(run_dir: Path) -> tuple[int, int, str]:
    best = run_dir / "weights" / "best.pt"
    candidate = best if best.is_file() and not best.is_symlink() else run_dir
    try:
        modified_ns = candidate.stat().st_mtime_ns
    except OSError:
        modified_ns = 0
    workspace_priority = 0
    try:
        workspace_root = get_runtime_paths().workspace.runs_root.resolve(strict=False)
        run_dir.resolve(strict=False).relative_to(workspace_root)
        workspace_priority = 1
    except (RuntimeError, ValueError):
        pass
    return modified_ns, workspace_priority, str(run_dir)


def compute_step_states(snapshot: Dict) -> Dict[int, str]:
    """由项目事实推出每一步的状态。"""
    has_project = bool(snapshot.get('project_id'))
    images = snapshot.get('image_count', 0)
    annotated = snapshot.get('annotated_count', 0)
    has_model = snapshot.get('weights') is not None
    has_runs = bool(snapshot.get('run_dirs'))

    states = {}

    if not has_project:
        states[STEP_IMPORT] = CURRENT
        for index in (STEP_ANNOTATE, STEP_TRAIN, STEP_RESULT, STEP_TEST):
            states[index] = LOCKED
        return states

    states[STEP_IMPORT] = DONE if images > 0 else CURRENT

    if images == 0:
        states[STEP_ANNOTATE] = LOCKED
    elif annotated == 0:
        states[STEP_ANNOTATE] = CURRENT
    elif annotated < images:
        states[STEP_ANNOTATE] = CURRENT
    else:
        states[STEP_ANNOTATE] = DONE

    if annotated == 0:
        states[STEP_TRAIN] = LOCKED
    elif has_model or has_runs:
        states[STEP_TRAIN] = DONE
    else:
        states[STEP_TRAIN] = CURRENT

    states[STEP_RESULT] = READY if has_runs else LOCKED
    states[STEP_TEST] = READY if has_model else LOCKED

    return states


def step_status_text(index: int, snapshot: Dict, states: Dict[int, str]) -> str:
    """侧边栏每一步下面那行小字：用数字说明当前进展。"""
    state = states.get(index, LOCKED)
    images = snapshot.get('image_count', 0)
    annotated = snapshot.get('annotated_count', 0)

    if not snapshot.get('project_id'):
        return "先创建项目" if index == STEP_IMPORT else "等待前一步"

    if index == STEP_IMPORT:
        return f"{images} 张图片" if images else "还没有图片"

    if index == STEP_ANNOTATE:
        if images == 0:
            return "等待导入图片"
        return f"已标注 {annotated}/{images}"

    if index == STEP_TRAIN:
        if state == LOCKED:
            return "等待标注"
        runs = len(snapshot.get('run_dirs', []))
        return f"已训练 {runs} 次" if runs else "还没训练过"

    if index == STEP_RESULT:
        return "等待训练" if state == LOCKED else "可以查看"

    if index == STEP_TEST:
        return "等待模型" if state == LOCKED else "模型已就绪"

    return ""


def get_blocker(index: int, snapshot: Dict) -> Optional[Dict]:
    """这一步能不能进？不能进就返回原因和「去哪里补」的入口。

    返回 None 表示可以进；否则返回:
        {'title', 'reason', 'action_text', 'action_index', 'bypass_text'(可选)}
    """
    has_project = bool(snapshot.get('project_id'))
    images = snapshot.get('image_count', 0)
    annotated = snapshot.get('annotated_count', 0)
    has_model = snapshot.get('weights') is not None
    has_runs = bool(snapshot.get('run_dirs'))

    if index in (PAGE_SETTINGS, PAGE_ABOUT, STEP_IMPORT):
        return None

    if not has_project:
        return {
            'title': '还没有选择项目',
            'reason': '先新建或选择一个项目。',
            'action_text': '去数据导入',
            'action_index': STEP_IMPORT,
        }

    if index == STEP_ANNOTATE and images == 0:
        return {
            'title': '项目里还没有图片',
            'reason': '先导入图片、视频或已有标注。',
            'action_text': '去导入图片',
            'action_index': STEP_IMPORT,
        }

    if index == STEP_TRAIN and annotated == 0:
        return {
            'title': '还没有标注好的图片',
            'reason': '至少标注一张图片再来训练。',
            'action_text': '去标注图片',
            'action_index': STEP_ANNOTATE,
        }

    if index == STEP_RESULT and not has_runs:
        return {
            'title': '还没有训练记录',
            'reason': '完成一次训练后，结果会出现在这里。',
            'action_text': '去训练模型',
            'action_index': STEP_TRAIN,
        }

    if index == STEP_TEST and not has_model:
        return {
            'title': '还没有可用的模型',
            'reason': '完成一次训练，或直接加载已有的权重文件（.pt）。',
            'action_text': '去训练模型',
            'action_index': STEP_TRAIN,
            'bypass_text': '加载已有模型',
        }

    return None


def get_next_action(snapshot: Dict, states: Dict[int, str], current_index: int) -> Optional[Dict]:
    """页头右上角那个「下一步」按钮：从当前这一步出发，接下来该去哪。

    原则：一页只催一个前进动作。如果这一步的动作就在页面里（比如训练页的
    「开始训练」），页头就不再放按钮跟它抢，返回 None。
    """
    if not snapshot.get('project_id'):
        return None

    images = snapshot.get('image_count', 0)
    annotated = snapshot.get('annotated_count', 0)
    has_runs = bool(snapshot.get('run_dirs'))
    has_model = snapshot.get('weights') is not None

    # 前置条件还没满足时，先把人送回该补的那一步
    if images == 0:
        target = STEP_IMPORT
    elif annotated == 0:
        target = STEP_ANNOTATE
    elif current_index == STEP_IMPORT:
        target = STEP_ANNOTATE if annotated < images else STEP_TRAIN
    elif current_index == STEP_ANNOTATE:
        target = STEP_TRAIN
    elif current_index == STEP_TRAIN:
        # 还没训练过：动作是页面里的「开始训练」，页头不插手
        target = STEP_RESULT if has_runs else None
    elif current_index == STEP_RESULT:
        target = STEP_TEST if has_model else None
    else:
        # 测试页是最后一步；设置/关于不属于主流程
        target = None

    if target is None or target == current_index:
        return None

    step = STEP_BY_INDEX[target]
    return {'text': f"下一步：{step['name']}", 'index': target}
