"""Marketing names for iPhone model identifiers (ProductType)."""
from __future__ import annotations

from typing import Optional

IPHONES = {
    "iPhone10,1": "iPhone 8", "iPhone10,4": "iPhone 8", "iPhone10,2": "iPhone 8 Plus", "iPhone10,5": "iPhone 8 Plus",
    "iPhone10,3": "iPhone X", "iPhone10,6": "iPhone X",
    "iPhone11,2": "iPhone XS", "iPhone11,4": "iPhone XS Max", "iPhone11,6": "iPhone XS Max", "iPhone11,8": "iPhone XR",
    "iPhone12,1": "iPhone 11", "iPhone12,3": "iPhone 11 Pro", "iPhone12,5": "iPhone 11 Pro Max",
    "iPhone12,8": "iPhone SE (2nd generation)",
    "iPhone13,1": "iPhone 12 mini", "iPhone13,2": "iPhone 12", "iPhone13,3": "iPhone 12 Pro", "iPhone13,4": "iPhone 12 Pro Max",
    "iPhone14,4": "iPhone 13 mini", "iPhone14,5": "iPhone 13", "iPhone14,2": "iPhone 13 Pro", "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,6": "iPhone SE (3rd generation)",
    "iPhone14,7": "iPhone 14", "iPhone14,8": "iPhone 14 Plus", "iPhone15,2": "iPhone 14 Pro", "iPhone15,3": "iPhone 14 Pro Max",
    "iPhone15,4": "iPhone 15", "iPhone15,5": "iPhone 15 Plus", "iPhone16,1": "iPhone 15 Pro", "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone17,3": "iPhone 16", "iPhone17,4": "iPhone 16 Plus", "iPhone17,1": "iPhone 16 Pro", "iPhone17,2": "iPhone 16 Pro Max",
    "iPhone17,5": "iPhone 16e",
    "iPhone18,3": "iPhone 17", "iPhone18,4": "iPhone Air", "iPhone18,1": "iPhone 17 Pro", "iPhone18,2": "iPhone 17 Pro Max",
}

_SIZES_GB = [16, 32, 64, 128, 256, 512, 1024, 2048]


def model_name(product_type: Optional[str]) -> str:
    """Unknown (newer) models keep their identifier, e.g. "iPhone19,1"."""
    return IPHONES.get(product_type or "", product_type or "iPhone")


def capacity_label(total_bytes: Optional[int]) -> Optional[str]:
    """Round the reported disk size up to the advertised one (a 256 GB iPhone reports ~255.9e9 bytes)."""
    if not total_bytes:
        return None
    gb = total_bytes / 1e9
    size = next((s for s in _SIZES_GB if gb <= s * 1.02), round(gb))
    return f"{size // 1024} TB" if size >= 1024 else f"{size} GB"
