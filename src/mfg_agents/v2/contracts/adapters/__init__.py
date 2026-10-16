"""IM 平台适配器集合。"""

from .wecom import WeComAdapter, WeComCryptoError

__all__ = ["WeComAdapter", "WeComCryptoError"]
