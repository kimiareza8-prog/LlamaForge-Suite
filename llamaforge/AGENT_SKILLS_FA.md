# سیستم Smart Skill و Agent در LlamaForge 0.34.0

این نسخه Agent را از «چند ابزار اینترنتی» به یک سیستم Skill چندمرحله‌ای تبدیل می‌کند. مدل GGUF همچنان روی کامپیوتر خودت اجرا می‌شود و خود مدل تصمیم می‌گیرد چه نوع کاری لازم است؛ Runtime فقط ابزار واقعی را اجرا و نتیجه را دوباره به مدل برمی‌گرداند.

## قوانین اجرا

مسیر عمومی Agent به شکل زیر است؛ سلام ساده مستقیماً پاسخ می‌گیرد و انتخاب Family در صورت پاسخ کامل Router، inference جداگانه ندارد:

1. **Understand** — یک inference واقعی روی مدل لوکال برای فهم هدف کاربر.
2. **Capability discovery** — مدل بین شاخه‌های عمومی Web، Calendar، Files، Browser، API و Extensions انتخاب می‌کند؛ Guardهای Runtime فقط جلوی جاافتادن شاخه‌های واضح را می‌گیرند.
3. **Skill shortlist** — به‌جای نشان‌دادن همه ابزارها، فقط چند Skill مرتبط به مدل نشان داده می‌شود. این برای مدل‌های 4B/7B بسیار مهم است.
4. **Planner** — مدل دقیقاً یک Skill و آرگومان‌های آن را انتخاب می‌کند.
5. **Preflight** — Runtime قبل از اجرا، وجود آرگومان‌ها، نصب Browser Skill، مجوز Local Workspace یا External Write و سایر پیش‌نیازها را بررسی می‌کند.
6. **Execution policy** — Runtime جلوی انتخاب‌های واضحاً پرهزینه/اشتباه را می‌گیرد؛ مثلاً برای «این URL باز می‌شود؟» اول `web_check`، برای خواندن صفحه `web_read` و Browser فقط در صورت نیاز.
7. **Execute → Observe** — Skill واقعاً اجرا می‌شود و نتیجهٔ ساختاریافته به مدل برمی‌گردد.
8. **Recovery** — خطاها به دسته‌هایی مثل timeout، 403، 401، 429، browser unavailable و permission blocked تقسیم می‌شوند و Fallbackهای مناسب به مدل داده می‌شود. فراخوانی شکست‌خوردهٔ یکسان دوباره اجرا نمی‌شود.
9. **Verify** — بعد از POST/PUT/PATCH/DELETE یا click/type/select، مدل تشویق می‌شود نتیجه را با read/snapshot بررسی کند.
10. **Final** — پاسخ از Stream واقعی مدل ساخته می‌شود؛ در پایان بودجه نیز فقط بر مبنای Observationهای موجود پاسخ می‌دهد.

## Skillهای داخلی

### Time & Calendar
- `calendar` — زمان/تاریخ واقعی سیستم، شمسی/میلادی، برنامهٔ روزانه و ساخت/ویرایش/حذف رویدادها.

### Files & Attachments
- `workspace_files` — خواندن پیوست و Workspace، PDF/Office/ZIP، ذخیرهٔ فایل، ساخت و ویرایش فایل متنی/کد و مدیریت پوشه‌ها.


### Web
- `web_check` — تست سریع URL: status، redirect، content-type و response time.
- `web_read` — خواندن متن، لینک‌ها و فرم‌های یک URL.
- `web_find` — پیدا کردن عبارت داخل یک صفحهٔ طولانی و برگرداندن snippetهای کوچک.
- `web_search` — جستجوی وب برای پیدا کردن URL و اطلاعات.
- `download_file` — دانلود فایل در پوشه Agent downloads با سقف حجم.

### HTTP / API
- `http_request` — GET/HEAD و با Write permission: POST/PUT/PATCH/DELETE.
- `connector_call` — اجرای operationهای OpenAPI Connector.

### Browser
در صورت نصب Selenium:
- `browser_open`
- `browser_snapshot`
- `browser_wait`
- `browser_scroll`
- `browser_hover`
- `browser_refresh`
- `browser_tabs` / `browser_new_tab` / `browser_switch_tab` / `browser_close_tab`
- `browser_back`
- `browser_close`

با Write permission:
- `browser_click`
- `browser_type`
- `browser_select`

Browser فقط برای صفحات JavaScript، فرم، لاگین، کلیک و تعامل واقعی است؛ برای خواندن صفحهٔ عادی استفاده نمی‌شود.

## Custom Skill v2

مسیر:

`~/.llamaforge/agent/skills`

نمونهٔ ساده:

```json
{
  "name": "product_lookup",
  "description": "Read a product from my store API",
  "method": "GET",
  "url": "https://example.com/api/product?id={url:id}",
  "parameters": {
    "id": {"type": "string", "description": "Product ID"}
  },
  "required": ["id"]
}
```

نمونهٔ v2:

```json
{
  "version": 2,
  "name": "create_ticket",
  "category": "support",
  "description": "Create a support ticket",
  "parameters": {
    "title": {"type": "string"},
    "body": {"type": "string"}
  },
  "required": ["title", "body"],
  "request": {
    "method": "POST",
    "url": "https://example.com/api/tickets",
    "headers": {"Authorization": "Bearer TOKEN"},
    "query": {"source": "llamaforge"},
    "json": {"title": "{title}", "body": "{body}"},
    "timeout": 30
  },
  "retry": {
    "attempts": 1,
    "statuses": [429, 500, 502, 503, 504]
  },
  "response": {
    "format": "json",
    "select": "data.ticket",
    "max_chars": 8000
  }
}
```

قابلیت‌های v2:
- Header سفارشی
- Query string
- JSON body یا raw body
- آرگومان اجباری
- Timeout
- Retry محدود برای خواندن GET/HEAD و statusهای مشخص؛ Write حتی با attempts بیشتر، خودکار تکرار نمی‌شود
- تشخیص `json/text/auto`
- استخراج Dot Path مثل `data.ticket.id`
- `{name}` برای مقدار خام، `{url:name}` برای URL-encoded و `{env:NAME}` برای مقدار Environment Variable

## Permissionها

- **Allow local calendar & file changes**: تغییرات داخل Workspace و تقویم محلی؛ پیش‌فرض روشن.
- **Allow external site actions**: POST/PUT/PATCH/DELETE و click/type/select روی سرویس‌های بیرونی؛ مستقل از Workspace و پیش‌فرض خاموش.
- **Allow localhost/private network**: دسترسی به localhost/LAN.
- **Hide Agent browser window**: اجرای Chrome به‌صورت Headless.
- **Maximum tool steps**: سقف مرحله‌های Agent.

## نکته برای مدل‌های کوچک

اضافه‌کردن Skill بیشتر به معنی نمایش هم‌زمان همهٔ Skillها نیست. Skill Registry ابتدا دستهٔ کار را انتخاب می‌کند و فقط مجموعهٔ مرتبط را جلوی مدل می‌گذارد. به همین دلیل می‌توان ده‌ها Skill نصب کرد بدون اینکه مدل 4B مجبور شود بین همهٔ آن‌ها انتخاب کند.

## تغییرات اجرایی 0.34.0

قرارداد عملیات، پیش‌نیازهای تایپ‌شده، رسید قابل استفادهٔ مجدد فایل/نوشتن، لغو خواندن‌های موازی، خواندن امن ZIP/TAR/Office و استریم مسیرهای خطا اضافه شده‌اند. ابزار جدیدی برای جمله‌های خاص ساخته نشده است. شرح دقیق رفتار و محدودیت‌ها در [SKILL_SYSTEM_FA.md](SKILL_SYSTEM_FA.md) و نتیجهٔ تست در [AUDIT_REPORT_FA.md](AUDIT_REPORT_FA.md) آمده است.
