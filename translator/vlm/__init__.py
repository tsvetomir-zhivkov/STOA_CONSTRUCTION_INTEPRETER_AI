"""Step 9: VLM checker behind a swappable interface (translator.vlm.base.VLMClient).

Only this package talks to a VLM API. Client by env STOA_VLM: gemini (default, REST) | fake | qwen (stub).
"""

from translator.vlm.base import BudgetExceeded, Crop, HoldoutError, VLMError, VLMResult, get_client

__all__ = ["BudgetExceeded", "Crop", "HoldoutError", "VLMError", "VLMResult", "get_client"]
