"""The application supports two pinned processing model IDs."""
import os

QWEN_MODEL = 'qwen3.8-omni-flash'
DEEPSEEK_MODEL = 'deepseek-v4.1-flash'


def deepseek_enabled():
    return os.getenv('DEEPSEEK_ENABLED', 'false').strip().lower() == 'true'


def validate_choice(choice: str | None) -> str:
    from app.media import InputError

    if choice is None:
        return QWEN_MODEL
    if choice not in (QWEN_MODEL, DEEPSEEK_MODEL):
        raise InputError('INVALID_MODEL', '不支持所选处理模型。', 422)
    if choice == DEEPSEEK_MODEL and not deepseek_enabled():
        raise InputError('MODEL_DISABLED', '所选处理模型暂未启用。请刷新后重新选择。', 409)
    return choice
