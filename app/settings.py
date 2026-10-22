import os
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get('WARGAME_DATA_DIR', ROOT / 'data'))
CONFIG = Path(os.environ.get('WARGAME_CONFIG', ROOT / 'config/settings.yaml'))


def settings():
    value = yaml.safe_load(CONFIG.read_text(encoding='utf-8'))
    c = value['competition']
    if not 1 <= c['max_calls_per_question'] <= 100 or not 0 < c['timeout_per_question_seconds'] <= 3600:
        raise ValueError('调用上限须为 1～100，超时须为 1～3600 秒')
    if value['evaluation']['weights'] != {'L2_main': .35, 'L3_main': .35, 'L1_sub': .15, 'L2_sub': .15}:
        raise ValueError('V1.1 使用固定的已确认评分权重')
    return value
