<?php
require __DIR__ . '/lib/bootstrap.php';
$config = aib_config();
$key = (string)($_GET['key'] ?? $_POST['owner_key'] ?? '');
if (!aib_owner_auth($key)) {
    http_response_code(403);
    ?><!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Bridge</title>
    <style>body{font-family:Tahoma,Arial;background:#f7f7f7;color:#181818;display:grid;place-items:center;min-height:100vh;margin:0}.box{max-width:560px;background:#fff;border:1px solid #ddd;border-radius:18px;padding:28px;line-height:1.9}</style><div class="box"><h2>دسترسی مدیریت</h2><p>لینک مدیریت شامل کلید مالک است. فایل <b>START-HERE.txt</b> داخل ZIP را باز کن و لینک مدیریت را از آن بردار.</p></div></html><?php exit;
}

$notice = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $action = (string)($_POST['do'] ?? '');
    if ($action === 'clear') {
        $n = aib_clear_messages();
        $notice = $n . ' پیام حذف شد.';
    }
}

$chatUrl = aib_url('index.php');
$agentUrl = aib_url('agent.php',['token'=>$config['agent_token']]);
$connectUrl = aib_url('connect.php',['token'=>$config['agent_token']]);
$waitUrl = aib_url('agent.php',['token'=>$config['agent_token'],'action'=>'inbox','wait'=>$config['agent_long_poll_seconds'],'claim'=>1]);
$streamUrl = aib_url('agent-stream.php',['token'=>$config['agent_token']]);
$agent = aib_agent_status();
?>
<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Bridge Manager</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:Tahoma,Arial,sans-serif;background:#f6f7f8;color:#161616}.wrap{max-width:1040px;margin:48px auto;padding:0 20px}.hero{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:24px}.brand{font-size:30px;font-weight:800;letter-spacing:-1px}.sub{color:#666;margin-top:8px}.badge{padding:9px 13px;border-radius:999px;background:#fff;border:1px solid #ddd;font-size:13px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.card{background:#fff;border:1px solid #e5e5e5;border-radius:20px;padding:22px;box-shadow:0 8px 30px rgba(0,0,0,.035)}.card.wide{grid-column:1/-1}h3{margin:0 0 8px;font-size:18px}.hint{font-size:13px;color:#777;line-height:1.8}.url{direction:ltr;text-align:left;background:#f4f4f4;border:1px solid #e3e3e3;border-radius:14px;padding:13px 14px;margin-top:12px;word-break:break-all;font-family:ui-monospace,Consolas,monospace;font-size:12px}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}button,a.btn{border:1px solid #d8d8d8;background:#fff;padding:10px 14px;border-radius:11px;font:inherit;cursor:pointer;text-decoration:none;color:#111}.primary{background:#111!important;color:#fff!important;border-color:#111!important}.danger{color:#9a1b1b}.notice{background:#eef8f0;border:1px solid #cfe7d3;padding:12px 14px;border-radius:12px;margin-bottom:18px}.steps{line-height:2}.mono{direction:ltr;text-align:left;font-family:ui-monospace,Consolas,monospace;background:#fafafa;padding:12px;border-radius:12px;border:1px dashed #ddd}@media(max-width:760px){.grid{grid-template-columns:1fr}.hero{align-items:flex-start;flex-direction:column}.card.wide{grid-column:auto}}
</style></head><body><main class="wrap">
<div class="hero"><div><div class="brand">AI Bridge <span style="font-size:14px;font-weight:500;color:#777">v<?=htmlspecialchars($config['version'])?></span></div><div class="sub">چت زنده + اتصال مستقیم LlamaForge + نمایش زنده مراحل اجرا</div></div><div class="badge">Agent: <?=$agent['online']?'آنلاین':'آفلاین'?></div></div>
<?php if($notice):?><div class="notice"><?=htmlspecialchars($notice)?></div><?php endif;?>
<div class="grid">
<section class="card"><h3>لینک صفحه چت</h3><div class="hint">کاربر این صفحه را باز می‌کند. تاریخچه بعد از Refresh باقی می‌ماند و پاسخ‌ها بدون Refresh ظاهر می‌شوند.</div><div class="url" id="chatUrl"><?=htmlspecialchars($chatUrl)?></div><div class="actions"><button onclick="copyText('chatUrl',this)">کپی لینک</button><a class="btn primary" href="<?=htmlspecialchars($chatUrl)?>" target="_blank">باز کردن چت</a></div></section>
<section class="card"><h3>اتصال مستقیم LlamaForge</h3><div class="hint">این لینک را در LlamaForge → Agent → Connected websites/apps وارد کن. توکن داخل همین URL قرار دارد و LlamaForge بعد از تست اتصال، سایت را در پس‌زمینه مانیتور می‌کند.</div><div class="url" id="connectUrl"><?=htmlspecialchars($connectUrl)?></div><div class="actions"><button class="primary" onclick="copyText('connectUrl',this)">کپی Connection URL</button></div></section>
<section class="card"><h3>لینک Agent عمومی</h3><div class="hint">این لینک قدیمی همچنان باقی مانده و برای Agentهای دیگر یا تست دستی پروتکل قابل استفاده است.</div><div class="url" id="agentUrl"><?=htmlspecialchars($agentUrl)?></div><div class="actions"><button onclick="copyText('agentUrl',this)">کپی لینک Agent</button></div></section>
<section class="card wide"><h3>حالت انتظار مداوم</h3><div class="hint">Agent بعد از هر پاسخ باید این Long‑Poll را فوراً دوباره باز کند. هر درخواست حداکثر حدود <?=intval($config['agent_long_poll_seconds'])?> ثانیه باز می‌ماند؛ این روش روی هاست اشتراکی پایدارتر از یک اتصال ۵ دقیقه‌ای است.</div><div class="url" id="waitUrl"><?=htmlspecialchars($waitUrl)?></div><div class="actions"><button onclick="copyText('waitUrl',this)">کپی Long‑Poll</button></div></section>
<section class="card"><h3>Stream اختیاری SSE</h3><div class="hint">برای Agentهایی که Server-Sent Events را پشتیبانی می‌کنند. برای اغلب ابزارهای AI همان Long‑Poll سازگارتر است.</div><div class="url" id="streamUrl"><?=htmlspecialchars($streamUrl)?></div><div class="actions"><button onclick="copyText('streamUrl',this)">کپی Stream</button></div></section>
<section class="card"><h3>هویت ثابت Bridge</h3><div class="hint">کلید مدیریت و توکن اتصال در اولین نصب ساخته می‌شوند و با Update Bridge عوض نمی‌شوند. این صفحه عمداً دکمهٔ تعویض توکن ندارد تا اتصال LlamaForge و لینک مدیریت پایدار بمانند.</div><form method="post" class="actions"><input type="hidden" name="owner_key" value="<?=htmlspecialchars($key)?>"><button class="danger" name="do" value="clear" onclick="return confirm('همه پیام‌ها حذف شوند؟')">پاک کردن همه چت‌ها</button></form></section>
<section class="card wide"><h3>سازگاری با AIهایی که فقط لینک باز می‌کنند</h3><div class="hint">در نسخه 2.1 برای هر پیام یک Capability موقت ساخته می‌شود. Agent URL برای همان پیام لینک GET آماده می‌دهد؛ هوش مصنوعی جواب را URL-encode می‌کند و فقط آن لینک را باز می‌کند. پاسخ‌های بلند نیز با append/finish چندتکه ثبت می‌شوند. Capability پس از پاسخ نهایی یا پایان اعتبار باطل می‌شود و توکن اصلی Agent داخل آن نیست.</div></section>
<section class="card wide"><h3>وضعیت LlamaForge متصل</h3><div class="hint">وقتی LlamaForge این سایت را اضافه کند، وضعیت اینجا آنلاین می‌شود. مراحل اجرای هر درخواست نیز مستقیم داخل صفحه چت کاربر نمایش داده می‌شود.</div><div class="mono">Status: <?=$agent['online']?'ONLINE':'OFFLINE'?><?php if(!empty($agent['agent_name'])):?> · <?=htmlspecialchars((string)$agent['agent_name'])?><?php endif;?><?php if(!empty($agent['model'])):?> · Model: <?=htmlspecialchars((string)$agent['model'])?><?php endif;?><?php if(!empty($agent['state'])):?> · State: <?=htmlspecialchars((string)$agent['state'])?><?php endif;?></div></section>
<section class="card wide"><h3>سازگاری Agentهای قدیمی</h3><div class="mono">Open this URL and follow its protocol exactly. Answer pending messages, submit each reply, then keep reopening next_wait_url and do not stop until I explicitly tell you to stop:<br><br><?=htmlspecialchars($agentUrl)?></div></section>
</div></main><script>function copyText(id,b){navigator.clipboard.writeText(document.getElementById(id).innerText).then(()=>{const old=b.innerText;b.innerText='کپی شد';setTimeout(()=>b.innerText=old,1200)})}</script></body></html>
