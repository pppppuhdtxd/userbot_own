"""
Module metadata for example_module.
============================================================

DATA ONLY — this file must not import anything from the project.
It is read by the module's __init__.py (which copies the fields onto
the Module subclass) and by help_handler.py. Keeping it import-free
makes an import cycle structurally impossible.

Fields
------
name       Logical module name; must match the module's file/folder name.
category   One of: cleaning, forward, info, social, reaction, system, general.
desc       One-line Persian description shown next to the name in `help`.
help_text  Compact help shown in the `help` listing.
help_extra Detailed help shown via `help <module>`.
"""

name = "example_module"

category = "general"
desc = "ماژول نمونه"

help_text = (
    "• `پینگ اضافی` | آزمایش سیستم ماژول‌های اضافی (پاسخ: 🏓 pong)\n"
)

help_extra = (
    "ماژول نمونه (modules_extra)\n"
    "\n"
    "این ماژول به‌صورت پیش‌فرض غیرفعال است. فقط برای آزمایش سیستم ماژول‌های\n"
    "اضافی و به‌عنوان الگو برای نوشتن ماژول جدید وجود دارد.\n"
    "\n"
    "فعال/غیرفعال‌سازی:\n"
    "• `.extra enable example_module`  | فعال‌سازی (بدون ری‌استارت)\n"
    "• `.extra disable example_module` | غیرفعال‌سازی (بدون ری‌استارت)\n"
    "\n"
    "دستور:\n"
    "• `پینگ اضافی` | پاسخ 🏓 pong — فقط در Saved Messages کار می‌کند\n"
    "\n"
    "نکات مهم:\n"
    "• پیام پاسخ پس از ۵ ثانیه به‌صورت خودکار حذف می‌شود\n"
    "• پس از اطمینان از کارکرد، این فایل را حذف کنید یا از آن به‌عنوان الگو استفاده کنید\n"
)
