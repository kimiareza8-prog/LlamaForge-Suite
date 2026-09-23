<?php
require __DIR__ . '/lib/bootstrap.php';

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    header('Allow: GET, POST, OPTIONS');
    http_response_code(204);
    exit;
}

$config = aib_config();
$action = (string)($_GET['action'] ?? $_POST['action'] ?? 'status');
$ownerHash = aib_current_owner_hash();

// Calendar and File Manager are browser-scoped. They intentionally expose a
// small set of generic primitives; the local model composes them as needed.
if (strpos($action, 'calendar_') === 0 || strpos($action, 'files_') === 0 || $action === 'file_download') {
    if ($ownerHash === '') aib_json_response(['ok'=>false,'error'=>'browser_identity_required'], 400);
}

if ($action === 'calendar_get') {
    aib_json_response(['ok'=>true,'calendar'=>aib_workspace_calendar($ownerHash),'revision'=>aib_workspace_revision($ownerHash),'server_time'=>aib_now()]);
}

if ($action === 'calendar_write' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    try {
        $in=aib_input(); $op=(string)($in['operation'] ?? '');
        $result=aib_workspace_calendar_write($ownerHash,$op,$in);
        aib_json_response(['ok'=>true]+$result);
    } catch (Throwable $e) { aib_json_response(['ok'=>false,'error'=>$e->getMessage()],400); }
}

if ($action === 'files_list') {
    try {
        $folder=(string)($_GET['folder'] ?? ''); $q=(string)($_GET['q'] ?? '');
        $result=aib_workspace_files_list($ownerHash,$folder,$q);
        aib_json_response(['ok'=>true]+$result+['revision'=>aib_workspace_revision($ownerHash)]);
    } catch (Throwable $e) { aib_json_response(['ok'=>false,'error'=>$e->getMessage()],400); }
}

if ($action === 'files_upload' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    try { $result=aib_workspace_upload($ownerHash,aib_input()); aib_json_response(['ok'=>true]+$result); }
    catch (Throwable $e) { aib_json_response(['ok'=>false,'error'=>$e->getMessage()],400); }
}

if ($action === 'files_action' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    try { $in=aib_input();$result=aib_workspace_file_action($ownerHash,(string)($in['operation']??''),$in);aib_json_response(['ok'=>true]+$result); }
    catch (Throwable $e) { aib_json_response(['ok'=>false,'error'=>$e->getMessage()],400); }
}

if ($action === 'file_download') {
    try {
        $id=aib_clean_message((string)($_GET['id']??''),100);[$row,$full]=aib_workspace_resolve_file($ownerHash,$id);
        header('Content-Type: '.((string)($row['mime']??'application/octet-stream')));
        header('Content-Length: '.(string)filesize($full));
        header("Content-Disposition: attachment; filename*=UTF-8''".rawurlencode((string)($row['name']??basename($full))));
        header('Cache-Control: private, no-store'); readfile($full); exit;
    } catch (Throwable $e) { aib_json_response(['ok'=>false,'error'=>$e->getMessage()],404); }
}

if ($action === 'send' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $in = aib_input();
    if ($ownerHash === '') aib_json_response(['ok'=>false,'error'=>'browser_identity_required'], 400);
    $session = trim((string)($in['session_id'] ?? ''));
    if (!aib_is_uuid($session)) $session = aib_uuid4();
    if (!aib_rate_limit_ok($ownerHash . ':' . $session)) aib_json_response(['ok'=>false,'error'=>'rate_limit'], 429);
    $message = aib_clean_message((string)($in['message'] ?? ''), (int)($config['max_message_chars'] ?? 5000));
    $attachments = aib_normalize_attachments($in['attachments'] ?? []);
    if ($message === '' && !$attachments) aib_json_response(['ok'=>false,'error'=>'empty_message'], 400);
    if (!empty($in['website'])) aib_json_response(['ok'=>false,'error'=>'blocked'], 400);
    $id = aib_uuid4();
    $requestedModelId = aib_clean_message((string)($in['model_id'] ?? ''), 100);
    $row = [
        'id' => $id,
        'session_id' => $session,
        'owner_hash' => $ownerHash,
        'question' => $message,
        'requested_model_id' => $requestedModelId ?: null,
        'attachments' => $attachments,
        'answer' => null,
        'partial_answer' => null,
        'status' => 'pending',
        'agent_name' => null,
        'created_at' => aib_now(),
        'claimed_at' => null,
        'answered_at' => null,
        'activity' => [],
    ];
    if (!aib_write_message($row)) aib_json_response(['ok'=>false,'error'=>'storage_error'], 500);
    aib_json_response(['ok'=>true,'message'=>aib_public_message($row),'session_id'=>$session]);
}

if ($action === 'model_select' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $in = aib_input();
    $modelId = aib_clean_message((string)($in['model_id'] ?? ''), 100);
    if ($modelId === '') aib_json_response(['ok'=>false,'error'=>'model_id_required'],400);
    $agent = aib_agent_status();
    $known = false;
    foreach (($agent['models'] ?? []) as $m) {
        if (is_array($m) && (string)($m['id'] ?? '') === $modelId) { $known = true; break; }
    }
    if (!$known) aib_json_response(['ok'=>false,'error'=>'model_not_available'],404);
    $control = aib_request_model($modelId);
    aib_json_response(['ok'=>true,'model_control'=>$control,'agent'=>aib_agent_status()]);
}

if ($action === 'model_stop' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $control = aib_request_model_action('unload', '');
    aib_json_response(['ok'=>true,'model_control'=>$control,'agent'=>aib_agent_status()]);
}

if ($action === 'model_status') {
    aib_json_response(['ok'=>true,'agent'=>aib_agent_status(),'model_control'=>aib_model_control(),'server_time'=>aib_now()]);
}

if ($action === 'stream') {
    aib_requeue_expired_processing();
    $session = trim((string)($_GET['session_id'] ?? ''));
    if (!aib_is_uuid($session)) aib_json_response(['ok'=>false,'error'=>'invalid_session'], 400);
    if ($ownerHash === '') aib_json_response(['ok'=>false,'error'=>'browser_identity_required'], 400);
    $since = trim((string)($_GET['since'] ?? ''));

    // True server-sent events for token/partial-answer delivery. Hosts or proxies
    // that buffer SSE are handled by the browser's long-poll fallback.
    @set_time_limit(0);
    @ignore_user_abort(true);
    @ini_set('zlib.output_compression', '0');
    header('Content-Type: text/event-stream; charset=utf-8');
    header('Cache-Control: no-cache, no-store, must-revalidate');
    header('X-Accel-Buffering: no');
    header('Connection: keep-alive');
    while (ob_get_level() > 0) { @ob_end_flush(); }

    $started = microtime(true);
    $lastPing = 0.0;
    $emit = function(string $event, array $payload): void {
        echo 'event: ' . $event . "\n";
        echo 'data: ' . json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . "\n\n";
        @flush();
    };

    while (!connection_aborted() && (microtime(true) - $started) < 45.0) {
        aib_requeue_expired_processing();
        $rows = array_map('aib_public_message', aib_session_history_owned($session, $ownerHash));
        $hash = hash('sha256', json_encode($rows, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
        if ($since === '' || !hash_equals($hash, $since)) {
            $emit('state', [
                'ok'=>true, 'changed'=>true, 'hash'=>$hash, 'session_id'=>$session,
                'messages'=>$rows, 'agent'=>aib_agent_status(), 'server_time'=>aib_now(),
            ]);
            $since = $hash;
        }
        $now = microtime(true);
        if (($now - $lastPing) >= 8.0) {
            echo ': ping ' . (string)time() . "\n\n";
            @flush();
            $lastPing = $now;
        }
        usleep(180000);
    }
    $emit('reconnect', ['ok'=>true,'hash'=>$since,'server_time'=>aib_now()]);
    exit;
}

if ($action === 'watch') {
    aib_requeue_expired_processing();
    $session = trim((string)($_GET['session_id'] ?? ''));
    if (!aib_is_uuid($session)) aib_json_response(['ok'=>false,'error'=>'invalid_session'], 400);
    if ($ownerHash === '') aib_json_response(['ok'=>false,'error'=>'browser_identity_required'], 400);
    $since = trim((string)($_GET['since'] ?? ''));
    $wait = max(1, min(22, (int)($_GET['wait'] ?? 18)));
    $started = microtime(true);
    do {
        $rows = array_map('aib_public_message', aib_session_history_owned($session, $ownerHash));
        $hash = hash('sha256', json_encode($rows, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
        if ($since === '' || !hash_equals($hash, $since)) {
            aib_json_response([
                'ok'=>true,
                'changed'=>true,
                'hash'=>$hash,
                'session_id'=>$session,
                'messages'=>$rows,
                'agent'=>aib_agent_status(),
                'server_time'=>aib_now(),
            ]);
        }
        usleep(300000);
    } while ((microtime(true) - $started) < $wait);

    aib_json_response([
        'ok'=>true,
        'changed'=>false,
        'hash'=>$since,
        'session_id'=>$session,
        'messages'=>null,
        'agent'=>aib_agent_status(),
        'server_time'=>aib_now(),
    ]);
}

if ($action === 'sessions') {
    if ($ownerHash === '') aib_json_response(['ok'=>false,'error'=>'browser_identity_required'], 400);
    aib_json_response(['ok'=>true,'sessions'=>aib_session_summaries_owned($ownerHash),'agent'=>aib_agent_status(),'server_time'=>aib_now()]);
}

if ($action === 'history') {
    aib_requeue_expired_processing();
    $session = trim((string)($_GET['session_id'] ?? ''));
    if (!aib_is_uuid($session)) aib_json_response(['ok'=>false,'error'=>'invalid_session'], 400);
    if ($ownerHash === '') aib_json_response(['ok'=>false,'error'=>'browser_identity_required'], 400);
    $rows = array_map('aib_public_message', aib_session_history_owned($session, $ownerHash));
    $hash = hash('sha256', json_encode($rows, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
    aib_json_response(['ok'=>true,'session_id'=>$session,'messages'=>$rows,'hash'=>$hash,'agent'=>aib_agent_status(),'server_time'=>aib_now()]);
}

if ($action === 'delete_session' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    if ($ownerHash === '') aib_json_response(['ok'=>false,'error'=>'browser_identity_required'], 400);
    $in = aib_input();
    $session = trim((string)($in['session_id'] ?? ''));
    if (!aib_is_uuid($session)) aib_json_response(['ok'=>false,'error'=>'invalid_session'], 400);
    $deleted = aib_delete_session_owned($session, $ownerHash);
    aib_json_response(['ok'=>true,'deleted'=>$deleted,'session_id'=>$session]);
}

if ($action === 'delete_all_history' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    if ($ownerHash === '') aib_json_response(['ok'=>false,'error'=>'browser_identity_required'], 400);
    $in = aib_input();
    $sessionIds = is_array($in['session_ids'] ?? null) ? $in['session_ids'] : [];
    foreach (array_slice($sessionIds, 0, 150) as $sid) {
        $sid = trim((string)$sid);
        if (aib_is_uuid($sid)) aib_claim_legacy_session($sid, $ownerHash);
    }
    $deleted = aib_delete_all_owned($ownerHash);
    aib_json_response(['ok'=>true,'deleted'=>$deleted]);
}

if ($action === 'cancel_message' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    if ($ownerHash === '') aib_json_response(['ok'=>false,'error'=>'browser_identity_required'], 400);
    $in = aib_input();
    $id = trim((string)($in['message_id'] ?? ''));
    $session = trim((string)($in['session_id'] ?? ''));
    $m = aib_read_message($id);
    if (!$m || ($m['session_id'] ?? '') !== $session || !aib_message_owned($m, $ownerHash)) aib_json_response(['ok'=>false,'error'=>'not_found'],404);
    $updated = aib_update_message($id, function($row) {
        if (in_array(($row['status'] ?? ''), ['answered','cancelled'], true)) return $row;
        $row['status'] = 'cancelled';
        $row['cancel_requested'] = true;
        $row['cancelled_at'] = aib_now();
        $row['partial_answer'] = null;
        $row['claimed_at'] = null;
        $row['activity'][] = aib_activity_event(['type'=>'cancel','phase'=>'complete','label'=>'پاسخ توسط کاربر متوقف شد','status'=>'error','ok'=>false]);
        return $row;
    });
    aib_json_response(['ok'=>true,'message'=>aib_public_message($updated ?: $m)]);
}

if ($action === 'message') {
    $id = trim((string)($_GET['id'] ?? ''));
    $session = trim((string)($_GET['session_id'] ?? ''));
    $m = aib_read_message($id);
    if (!$m || ($m['session_id'] ?? '') !== $session || !aib_message_owned($m, $ownerHash)) aib_json_response(['ok'=>false,'error'=>'not_found'], 404);
    aib_json_response(['ok'=>true,'message'=>aib_public_message($m),'agent'=>aib_agent_status()]);
}

aib_json_response(['ok'=>true,'version'=>$config['version'],'agent'=>aib_agent_status(),'poll_ms'=>(int)$config['chat_poll_ms'],'server_time'=>aib_now()]);
