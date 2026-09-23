# اتصال سایت به LlamaForge

1. نسخه Sync شده AI Bridge را روی هاست PHP نصب کن.
2. صفحه `manage.php` را باز کن و **LlamaForge Connection URL** را کپی کن.
3. در LlamaForge به `Agent` برو و در بخش **Connected websites / apps** همان URL را وارد کن. اگر URL توکن ندارد، Token را جداگانه وارد کن.
4. روی Connect بزن. اگر مدل محلی آماده باشد، LlamaForge به صورت پس‌زمینه سایت را مانیتور می‌کند.
5. کاربر در سایت پیام می‌دهد؛ پیام Claim می‌شود و دقیقاً از AgentEngine و Skill System خود LlamaForge عبور می‌کند.
6. مراحل `Understand → Skills → Plan → Execute → Observe → Final` روی Frontend سایت زنده نمایش داده می‌شوند. پاسخ متنی نیز در حین تولید با `partial_answer` قابل مشاهده است.

## رفتار اتصال

- وقتی مدل آماده نیست، LlamaForge پیام‌ها را Claim نمی‌کند؛ سایت وضعیت «مدل آماده نیست» را می‌بیند.
- هنگام اجرای طولانی، heartbeat مخفی Lease پیام را تمدید می‌کند تا پیام دوباره pending نشود.
- مجوز Write خود LlamaForge همچنان روی POST/PUT/DELETE و Browser actions اعمال می‌شود. انتقال پاسخ نهایی به Bridge جزء Transport اتصال است و به Write Skill وابسته نیست.
- Connection URL می‌تواند Token را در Query داشته باشد. LlamaForge Token را از URL جدا می‌کند و در صورت وجود Credential Vault سیستم‌عامل آن را آنجا ذخیره می‌کند.
- متن Activity قبل از ارسال به سایت برای الگوهای رایج Token/Bearer redaction می‌شود.
