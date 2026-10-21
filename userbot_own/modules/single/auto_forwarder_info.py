"""
Module metadata for AutoForwarder.
============================================================

DATA ONLY — this file must not import anything from the project.
It is read by the module's __init__.py (which copies the fields
onto the Module subclass) and by help_handler.py. Keeping it
import-free makes an import cycle structurally impossible.

Fields
------
name       Logical module name; must match the module's file/folder name.
category   One of: cleaning, forward, info, social, reaction, system, general.
desc       One-line Persian description shown next to the name in `help`.
help_text  Compact help shown in the `help` listing.
help_extra Detailed help shown via `help <module>`.
"""

name = "auto_forwarder"

category = "forward"
desc = "فوروارد خودکار"

help_text = (
    "• `autofor <type> on` | فعال‌سازی فوروارد\n"
    "• `autofor <type> off` | غیرفعال‌سازی فوروارد\n"
    "• `forward status` | نمایش وضعیت فعلی\n"
)

help_extra = (
    "فوروارد خودکار - فوروارد خودکار پیام‌های ربات به همان ربات\n"
    "\n"
    "دستور اصلی:\n"
    "• `autofor <type> on` | فعال‌سازی فوروارد\n"
    "• `autofor <type> off` | غیرفعال‌سازی فوروارد\n"
    "• `forward status` | نمایش وضعیت فعلی (فقط در Saved Messages)\n"
    "\n"
    "انواع پیام (type):\n"
    "• `txt` | پیام‌های متنی محض (فوروارد فوری)\n"
    "• `link` | پیام‌های لینک (WebPage preview) — فوروارد فوری\n"
    "• `pic` | عکس‌ها\n"
    "• `vid` | ویدیوها و GIF\n"
    "• `file` | فایل‌های ضمیمه (شامل audio)\n"
    "• `caption` | ارسال caption پیام اصلی\n"
    "• `all` | همه انواع به جز caption\n"
    "\n"
    "مکان استفاده:\n"
    "• در Saved Messages | تنظیم کلی برای همه ربات‌ها\n"
    "• در چت یک ربات | تنظیم مخصوص همان ربات\n"
    "\n"
    "منطق فوروارد:\n"
    "• `txt` | بلافاصله به همان ربات فوروارد می‌شود و پیام اصلی حذف می‌شود\n"
    "• `link` | همان رفتار `txt` — لینک به‌همراه کپشن به همان ربات ارسال می‌شود\n"
    "• `pic` / `vid` / `file` | در قالب album گروه‌بندی می‌شوند (حداکثر ۱۰ تایی)\n"
    "• `caption` | اگر on باشد، caption پیام اصلی هم ارسال می‌شود\n"
    "• `sticker` | هیچ‌وقت فوروارد نمی‌شود (حتی اگر فعال باشد)\n"
    "• `audio` | تک‌تک فوروارد می‌شود (نمی‌تواند در album با عکس/ویدیو باشد)\n"
    "\n"
    "مثال‌ها:\n"
    "• `autofor txt on` | فعال‌سازی فوروارد پیام‌های متنی\n"
    "• `autofor pic on` | فعال‌سازی فوروارد عکس‌ها\n"
    "• `autofor caption on` | ارسال caption پیام‌های اصلی\n"
    "• `autofor all off` | غیرفعال‌سازی همه انواع\n"
    "• `forward status` | نمایش وضعیت فعلی\n"
    "\n"
    "نکات مهم:\n"
    "• پیام‌های اصلی پس از فوروارد موفق حذف می‌شوند\n"
    "• اگر فوروارد album شکست بخورد، پیام‌ها تک‌تک فوروارد می‌شوند\n"
    "• گروه‌بندی media با تأخیر ۱ ثانیه انجام می‌شود\n"
    "• تنظیمات در `autoforward.json` ذخیره می‌شود\n"
    "• فقط پیام‌های ربات‌ها فوروارد می‌شوند\n"
    "• فقط در چت‌های خصوصی (Private Chat) کار می‌کند\n"
    "• همه تنظیمات به‌صورت پیش‌فرض خاموش هستند\n"
)

