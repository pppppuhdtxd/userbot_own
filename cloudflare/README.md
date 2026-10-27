# CF Worker — Telegram WebSocket Relay (v3.2.7 "cf" mode)

این پوشه حاوی یک فایل است: [`tg-relay-worker.js`](tg-relay-worker.js) —
یک Cloudflare Worker تک‌فایلی که بایت‌های WebSocket را بین یوزربات و
نقاط پایانی رسمی MTProto-بالا-WebSocket تلگرام رله می‌کند.

**چرا:** در شبکه‌های فیلترشده، DNS برای `*.web.telegram.org` به IPهای
خام تلگرام (149.154.x.x) resolve می‌شود که ISP بلاک می‌کند — اما
`*.workers.dev` به IPهای واقعی لبه Cloudflare resolve می‌شود که
قابل دسترسی‌اند. اتصال خروجی Worker به تلگرام از شبکه خود
Cloudflare اجرا می‌شود، خارج از دسترس فیلتر ISP.

**امنیت:** relay فقط به مقصدهای منطبق بر
`^[a-z]+(-1)?\.web\.telegram\.org$` شماره می‌گیرد — نقاط پایانی
رسمی وب تلگرام. Worker هیچ‌وقت نمی‌تواند به عنوان پراکسی باز
سوءاستفاده شود.

## نصب (5 دقیقه، پلن رایگان کافی است)

1. وارد [dash.cloudflare.com](https://dash.cloudflare.com) شوید
2. **Workers & Pages → Create → Worker** → یک نام انتخاب کنید (مثلاً `userbot-relay`) → **Deploy**
3. **Edit code** را کلیک کنید، کل محتوای [`tg-relay-worker.js`](tg-relay-worker.js) را جایگذاری کنید، **Deploy** کنید
4. URL را یادداشت کنید: `https://userbot-relay.<your-subdomain>.workers.dev`
5. در یوزربات: `run.bat` → **[3] Connection settings** → **[2] Change connection mode** → **[4] Cloudflare - with Worker** → دامنه Worker را وارد کنید
   (یا بدون منو: `python settings.py set-cf-worker userbot-relay.<your-subdomain>.workers.dev`)

پلن رایگان 100k درخواست/روز را شامل می‌شود — برای یک یوزربات
چند اکانتی به طور قابل توجهی بیشتر از نیاز است.

## تست سریع بعد از نصب

مرورگر یا `curl`:

```
curl -i https://userbot-relay.<your-subdomain>.workers.dev/venus-1.web.telegram.org/apiws
```

انتظار: `400 expected websocket upgrade` (این کار می‌کند!؛ این یعنی
Worker فعال است و هدر WebSocket را درست درخواست می‌کند). هر پاسخ
دیگری (404/403/500) یعنی یک مشکل در محل دهی وجود دارد.

## مسیر پوشه Worker

هیچ چیز دیگری در این پوشه زندگی نمی‌کند؛ worker یک فایل JS مستقل
است بدون build step، بدون وابستگی، بدون wrangler مورد نیاز —
همه چیز کافی است copy-paste در داشبورد انجام شود.

---

*بخشی از طرح اتصال v3.2.7. فاز 3 (transport WebSocket در
`core/connection_ws.py`) در سمت Python این را می‌خواند.*
