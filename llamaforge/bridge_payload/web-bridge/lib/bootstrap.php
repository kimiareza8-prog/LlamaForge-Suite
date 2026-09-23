<?php
if (PHP_VERSION_ID < 70400) {
    http_response_code(500);
    exit('AI Bridge requires PHP 7.4 or newer.');
}

define('AIB_ROOT', dirname(__DIR__));
define('AIB_DATA', AIB_ROOT . '/data');
define('AIB_MESSAGES', AIB_DATA . '/messages');
define('AIB_RATELIMITS', AIB_DATA . '/ratelimits');
define('AIB_WORKSPACES', AIB_DATA . '/workspaces');
define('AIB_CONFIG_FILE', AIB_DATA . '/config.php');


function aib_random_secret(string $prefix): string {
    return $prefix . rtrim(strtr(base64_encode(random_bytes(36)), '+/', '-_'), '=');
}

function aib_default_config(bool $withSecrets = false): array {
    return [
        'version' => '3.9.0-live-stream-files',
        'app_name' => 'AI Bridge',
        // Secrets are generated exactly once by aib_ensure_config(). Normal reads
        // never invent replacement credentials if config.php is temporarily unavailable.
        'agent_token' => $withSecrets ? aib_random_secret('aib_') : '',
        'owner_key' => $withSecrets ? aib_random_secret('own_') : '',
        'browser_get_reply' => true,
        'agent_long_poll_seconds' => 20,
        'processing_lease_seconds' => 90,
        'chat_poll_ms' => 850,
        'max_message_chars' => 5000,
        'max_answer_chars' => 50000,
        'reply_capability_ttl_seconds' => 900,
        'browser_get_chunk_chars' => 1400,
        'max_activity_events' => 80,
    ];
}

function aib_ensure_config(): void {
    if (is_file(AIB_CONFIG_FILE)) return;
    if (!is_dir(AIB_DATA) && !mkdir(AIB_DATA, 0750, true) && !is_dir(AIB_DATA)) return;
    $config = aib_default_config(true);
    $php = "<?php\nreturn " . var_export($config, true) . ";\n";
    @file_put_contents(AIB_CONFIG_FILE, $php, LOCK_EX);
}

aib_ensure_config();

function aib_config(): array {
    static $config = null;
    if ($config === null) {
        $loaded = is_file(AIB_CONFIG_FILE) ? require AIB_CONFIG_FILE : [];
        $config = is_array($loaded) ? array_merge(aib_default_config(false), $loaded) : aib_default_config(false);
        // Code version is authoritative. The data/config file is intentionally
        // preserved across one-click updates, so it must not pin an old version.
        $versionFile = AIB_ROOT . '/VERSION';
        if (is_file($versionFile)) {
            $version = trim((string)@file_get_contents($versionFile));
            if ($version !== '') $config['version'] = $version;
        }
    }
    return $config;
}

function aib_write_config(array $config): bool {
    $php = "<?php\nreturn " . var_export($config, true) . ";\n";
    return aib_atomic_write(AIB_CONFIG_FILE, $php);
}

function aib_atomic_write(string $path, string $contents): bool {
    $dir = dirname($path);
    if (!is_dir($dir) && !mkdir($dir, 0750, true) && !is_dir($dir)) return false;
    $tmp = tempnam($dir, 'aibtmp_');
    if ($tmp === false) return false;
    $ok = file_put_contents($tmp, $contents, LOCK_EX) !== false;
    if ($ok) $ok = @rename($tmp, $path);
    if (!$ok) @unlink($tmp);
    return $ok;
}

function aib_json_response(array $data, int $status = 200): void {
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
    header('Pragma: no-cache');
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
    exit;
}

function aib_request_json(): array {
    $raw = file_get_contents('php://input');
    if (!$raw) return [];
    $data = json_decode($raw, true);
    return is_array($data) ? $data : [];
}

function aib_input(): array {
    $json = aib_request_json();
    if ($json) return array_merge($_REQUEST, $json);
    return $_REQUEST;
}

function aib_uuid4(): string {
    $data = random_bytes(16);
    $data[6] = chr((ord($data[6]) & 0x0f) | 0x40);
    $data[8] = chr((ord($data[8]) & 0x3f) | 0x80);
    return vsprintf('%s%s-%s-%s-%s-%s%s%s', str_split(bin2hex($data), 4));
}

function aib_is_uuid(string $value): bool {
    return (bool) preg_match('/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i', $value);
}

function aib_now(): string {
    return gmdate('c');
}

function aib_epoch_from_iso(?string $iso): int {
    if (!$iso) return 0;
    $t = strtotime($iso);
    return $t === false ? 0 : $t;
}

function aib_base_url(): string {
    $https = (!empty($_SERVER['HTTPS']) && strtolower((string)$_SERVER['HTTPS']) !== 'off') || ((string)($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https');
    $scheme = $https ? 'https' : 'http';
    $host = $_SERVER['HTTP_HOST'] ?? 'localhost';
    $script = str_replace('\\', '/', $_SERVER['SCRIPT_NAME'] ?? '/');
    $dir = rtrim(str_replace(basename($script), '', $script), '/');
    return $scheme . '://' . $host . $dir;
}

function aib_url(string $file, array $query = []): string {
    $url = aib_base_url() . '/' . ltrim($file, '/');
    if ($query) $url .= '?' . http_build_query($query, '', '&', PHP_QUERY_RFC3986);
    return $url;
}

function aib_clean_message(string $text, int $max): string {
    $text = trim(str_replace("\0", '', $text));
    if (function_exists('mb_substr')) return mb_substr($text, 0, $max);
    return substr($text, 0, $max);
}

function aib_normalize_attachments($rows): array {
    if (!is_array($rows)) return [];
    $out = [];
    foreach (array_slice($rows, 0, 8) as $raw) {
        if (!is_array($raw)) continue;
        $kind = strtolower(aib_clean_message((string)($raw['kind'] ?? ''), 16));
        $name = aib_clean_message((string)($raw['name'] ?? 'attachment'), 180);
        $type = aib_clean_message((string)($raw['type'] ?? ''), 100);
        $size = max(0, (int)($raw['size'] ?? 0));
        if ($kind === 'image') {
            $data = (string)($raw['data_url'] ?? '');
            if (!preg_match('#^data:image/(?:png|jpeg|jpg|webp|gif);base64,[A-Za-z0-9+/=\r\n]+$#i', $data)) continue;
            if (strlen($data) > 28 * 1024 * 1024) continue;
            $out[] = ['kind'=>'image','name'=>$name,'type'=>$type ?: 'image/jpeg','size'=>$size,'data_url'=>$data];
        } elseif ($kind === 'text') {
            $text = aib_clean_message((string)($raw['text'] ?? ''), 800000);
            if ($text === '') continue;
            $out[] = ['kind'=>'text','name'=>$name,'type'=>$type ?: 'text/plain','size'=>$size,'text'=>$text];
        } elseif ($kind === 'file') {
            $data = (string)($raw['data_url'] ?? '');
            if (!preg_match('#^data:[A-Za-z0-9.+/-]+;base64,[A-Za-z0-9+/=\r\n]+$#i', $data)) continue;
            if (strlen($data) > 28 * 1024 * 1024) continue;
            $out[] = ['kind'=>'file','name'=>$name,'type'=>$type ?: 'application/octet-stream','size'=>$size,'data_url'=>$data];
        }
    }
    return $out;
}

function aib_public_attachments($rows): array {
    $out = [];
    foreach (aib_normalize_attachments($rows) as $a) {
        $out[] = ['kind'=>$a['kind'],'name'=>$a['name'],'type'=>$a['type'],'size'=>$a['size']];
    }
    return $out;
}

function aib_message_path(string $id): string {
    return AIB_MESSAGES . '/' . basename($id) . '.json';
}

function aib_read_json(string $path): ?array {
    if (!is_file($path)) return null;
    $raw = @file_get_contents($path);
    if ($raw === false || $raw === '') return null;
    $data = json_decode($raw, true);
    return is_array($data) ? $data : null;
}

function aib_write_message(array $message): bool {
    if (empty($message['id'])) return false;
    return aib_atomic_write(aib_message_path($message['id']), json_encode($message, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT));
}

function aib_read_message(string $id): ?array {
    if (!aib_is_uuid($id)) return null;
    return aib_read_json(aib_message_path($id));
}

function aib_update_message(string $id, callable $mutator): ?array {
    if (!aib_is_uuid($id)) return null;
    $path = aib_message_path($id);
    if (!is_file($path)) return null;
    $fp = @fopen($path, 'c+');
    if (!$fp) return null;
    if (!flock($fp, LOCK_EX)) { fclose($fp); return null; }
    rewind($fp);
    $raw = stream_get_contents($fp);
    $data = json_decode($raw ?: '', true);
    if (!is_array($data)) { flock($fp, LOCK_UN); fclose($fp); return null; }
    $new = $mutator($data);
    if (!is_array($new)) $new = $data;
    $json = json_encode($new, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
    ftruncate($fp, 0);
    rewind($fp);
    fwrite($fp, $json);
    fflush($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    return $new;
}

function aib_all_messages(): array {
    $items = [];
    foreach (glob(AIB_MESSAGES . '/*.json') ?: [] as $file) {
        $m = aib_read_json($file);
        if (is_array($m) && !empty($m['id'])) $items[] = $m;
    }
    usort($items, function($a, $b) {
        return strcmp((string)($a['created_at'] ?? ''), (string)($b['created_at'] ?? ''));
    });
    return $items;
}

function aib_session_history(string $sessionId): array {
    if (!aib_is_uuid($sessionId)) return [];
    $out = [];
    foreach (aib_all_messages() as $m) {
        if (($m['session_id'] ?? '') === $sessionId) $out[] = $m;
    }
    return $out;
}

function aib_history_for_agent(string $sessionId, int $limit = 30): array {
    $rows = aib_session_history($sessionId);
    if (count($rows) > $limit) $rows = array_slice($rows, -$limit);
    $history = [];
    foreach ($rows as $row) {
        $history[] = ['role' => 'user', 'content' => (string)($row['question'] ?? ''), 'message_id' => $row['id'] ?? null];
        if (!empty($row['answer'])) {
            $history[] = ['role' => 'assistant', 'content' => (string)$row['answer'], 'message_id' => $row['id'] ?? null];
        }
    }
    return $history;
}


function aib_browser_key(): string {
    $key = trim((string)($_SERVER['HTTP_X_AIB_BROWSER'] ?? $_REQUEST['browser_key'] ?? ''));
    if ($key === '' || strlen($key) < 24 || strlen($key) > 240) return '';
    if (!preg_match('/^[A-Za-z0-9._~-]+$/', $key)) return '';
    return $key;
}

function aib_owner_hash_from_key(string $key): string {
    return $key === '' ? '' : hash('sha256', 'aib-browser-v1|' . $key);
}

function aib_current_owner_hash(): string {
    return aib_owner_hash_from_key(aib_browser_key());
}

function aib_claim_legacy_session(string $sessionId, string $ownerHash): void {
    if (!aib_is_uuid($sessionId) || $ownerHash === '') return;
    $rows = aib_session_history($sessionId);
    if (!$rows) return;
    foreach ($rows as $row) {
        $existing = (string)($row['owner_hash'] ?? '');
        if ($existing !== '' && !hash_equals($existing, $ownerHash)) return;
    }
    foreach ($rows as $row) {
        if (!empty($row['owner_hash'])) continue;
        aib_update_message((string)$row['id'], function($m) use ($ownerHash) {
            if (empty($m['owner_hash'])) $m['owner_hash'] = $ownerHash;
            return $m;
        });
    }
}

function aib_session_history_owned(string $sessionId, string $ownerHash): array {
    if (!aib_is_uuid($sessionId) || $ownerHash === '') return [];
    aib_claim_legacy_session($sessionId, $ownerHash);
    return array_values(array_filter(aib_session_history($sessionId), function($m) use ($ownerHash) {
        $stored = (string)($m['owner_hash'] ?? '');
        return $stored !== '' && hash_equals($stored, $ownerHash);
    }));
}

function aib_message_owned(array $m, string $ownerHash): bool {
    if ($ownerHash === '') return false;
    $stored = (string)($m['owner_hash'] ?? '');
    return $stored !== '' && hash_equals($stored, $ownerHash);
}

function aib_session_summaries_owned(string $ownerHash): array {
    if ($ownerHash === '') return [];
    $map = [];
    foreach (aib_all_messages() as $m) {
        if (!aib_message_owned($m, $ownerHash)) continue;
        $sid = (string)($m['session_id'] ?? '');
        if (!aib_is_uuid($sid)) continue;
        if (!isset($map[$sid])) {
            $title = preg_replace('/\s+/u', ' ', trim((string)($m['question'] ?? ''))) ?: 'گفتگوی جدید';
            if (function_exists('mb_substr')) $title = mb_substr($title, 0, 52); else $title = substr($title, 0, 52);
            $map[$sid] = ['id'=>$sid,'title'=>$title,'updated'=>0];
        }
        $stamp = max(aib_epoch_from_iso($m['created_at'] ?? null), aib_epoch_from_iso($m['answered_at'] ?? null), aib_epoch_from_iso($m['cancelled_at'] ?? null));
        if ($stamp > (int)$map[$sid]['updated']) $map[$sid]['updated'] = $stamp;
    }
    $rows = array_values($map);
    usort($rows, fn($a,$b)=>(int)$b['updated'] <=> (int)$a['updated']);
    foreach ($rows as &$row) $row['updated'] = ((int)$row['updated']) * 1000;
    unset($row);
    return array_slice($rows, 0, 100);
}

function aib_delete_session_owned(string $sessionId, string $ownerHash): int {
    $count = 0;
    foreach (aib_session_history_owned($sessionId, $ownerHash) as $m) {
        $path = aib_message_path((string)($m['id'] ?? ''));
        if (is_file($path) && @unlink($path)) $count++;
    }
    return $count;
}

function aib_delete_all_owned(string $ownerHash): int {
    if ($ownerHash === '') return 0;
    $count = 0;
    foreach (aib_all_messages() as $m) {
        if (!aib_message_owned($m, $ownerHash)) continue;
        $path = aib_message_path((string)($m['id'] ?? ''));
        if (is_file($path) && @unlink($path)) $count++;
    }
    return $count;
}

function aib_history_for_agent_owned(string $sessionId, string $ownerHash, int $limit = 30): array {
    $rows = $ownerHash !== '' ? aib_session_history_owned($sessionId, $ownerHash) : aib_session_history($sessionId);
    if (count($rows) > $limit) $rows = array_slice($rows, -$limit);
    $history = [];
    foreach ($rows as $row) {
        $history[] = ['role'=>'user','content'=>(string)($row['question'] ?? ''),'message_id'=>$row['id'] ?? null];
        if (!empty($row['answer'])) $history[] = ['role'=>'assistant','content'=>(string)$row['answer'],'message_id'=>$row['id'] ?? null];
    }
    return $history;
}

function aib_rate_limit_ok(string $sessionId, int $limit = 40, int $window = 300): bool {
    $path = AIB_RATELIMITS . '/' . hash('sha256', $sessionId) . '.json';
    $now = time();
    $data = aib_read_json($path) ?: ['hits' => []];
    $hits = array_values(array_filter($data['hits'] ?? [], fn($t) => is_int($t) && $t > $now - $window));
    if (count($hits) >= $limit) return false;
    $hits[] = $now;
    aib_atomic_write($path, json_encode(['hits' => $hits]));
    return true;
}

function aib_agent_token(): string {
    $header = trim((string)($_SERVER['HTTP_X_AIB_TOKEN'] ?? ''));
    if ($header !== '') return $header;
    $auth = trim((string)($_SERVER['HTTP_AUTHORIZATION'] ?? ''));
    if (stripos($auth, 'Bearer ') === 0) return trim(substr($auth, 7));
    return trim((string)($_REQUEST['token'] ?? ''));
}

function aib_agent_auth(): bool {
    $stored = (string)(aib_config()['agent_token'] ?? '');
    $given = aib_agent_token();
    return $stored !== '' && $given !== '' && hash_equals($stored, $given);
}

function aib_owner_auth(?string $key = null): bool {
    $stored = (string)(aib_config()['owner_key'] ?? '');
    $given = $key ?? (string)($_REQUEST['key'] ?? $_REQUEST['owner_key'] ?? '');
    return $stored !== '' && $given !== '' && hash_equals($stored, $given);
}

function aib_agent_status_path(): string { return AIB_DATA . '/agent-status.json'; }
function aib_model_control_path(): string { return AIB_DATA . '/model-control.json'; }

function aib_model_control(): array {
    $row = aib_read_json(aib_model_control_path()) ?: [];
    return is_array($row) ? $row : [];
}

function aib_request_model_action(string $action, string $modelId = ''): array {
    $action = strtolower(trim($action));
    if (!in_array($action, ['load','unload'], true)) $action = 'load';
    $modelId = aib_clean_message($modelId, 100);
    if ($action === 'load' && $modelId === '') return [];
    $current = aib_model_control();
    if ($action === 'load' && ($current['action'] ?? 'load') === 'load' && ($current['model_id'] ?? '') === $modelId && in_array(($current['status'] ?? ''), ['pending','loading'], true)) return $current;
    if ($action === 'unload' && ($current['action'] ?? '') === 'unload' && in_array(($current['status'] ?? ''), ['pending','loading'], true)) return $current;
    $row = [
        'request_id' => 'mreq_' . bin2hex(random_bytes(10)),
        'action' => $action,
        'model_id' => $action === 'load' ? $modelId : '',
        'status' => 'pending',
        'requested_at' => aib_now(),
        'updated_at' => aib_now(),
        'error' => null,
    ];
    aib_atomic_write(aib_model_control_path(), json_encode($row, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT));
    return $row;
}

function aib_request_model(string $modelId): array {
    return aib_request_model_action('load', $modelId);
}

function aib_update_model_control_result(array $result): array {
    $current = aib_model_control();
    $requestId = (string)($result['request_id'] ?? '');
    if ($requestId === '' || $requestId !== (string)($current['request_id'] ?? '')) return $current;
    $ok = !empty($result['ok']);
    $state = (string)($result['state'] ?? '');
    if ($ok && in_array($state, ['ready','stopped','unloaded'], true)) $current['status'] = $state === 'ready' ? 'ready' : 'stopped';
    else $current['status'] = $ok ? 'loading' : 'error';
    $current['error'] = $ok ? null : aib_clean_message((string)($result['error'] ?? 'model_load_failed'), 500);
    $current['updated_at'] = aib_now();
    if (!empty($result['model'])) $current['model_name'] = aib_clean_message((string)$result['model'], 180);
    aib_atomic_write(aib_model_control_path(), json_encode($current, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT));
    return $current;
}

function aib_public_models($rows): array {
    if (!is_array($rows)) return [];
    $out = [];
    foreach (array_slice($rows, 0, 100) as $m) {
        if (!is_array($m)) continue;
        $id = aib_clean_message((string)($m['id'] ?? ''), 100);
        if ($id === '') continue;
        $out[] = [
            'id'=>$id,
            'name'=>aib_clean_message((string)($m['name'] ?? 'Model'), 180),
            'architecture'=>aib_clean_message((string)($m['architecture'] ?? ''), 80),
            'quantization'=>aib_clean_message((string)($m['quantization'] ?? ''), 80),
            'size_gb'=>round((float)($m['size_gb'] ?? 0), 2),
            'loaded'=>!empty($m['loaded']),
            'selected'=>!empty($m['selected']),
            'vision_capable'=>!empty($m['vision_capable']),
        ];
    }
    return $out;
}

function aib_touch_agent(string $agentName = 'External AI Agent', array $meta = []): void {
    $current = aib_read_json(aib_agent_status_path()) ?: [];
    $row = array_merge($current, $meta, [
        'last_seen' => aib_now(),
        'agent_name' => $agentName,
    ]);
    aib_atomic_write(aib_agent_status_path(), json_encode($row, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT));
}

function aib_agent_status(): array {
    $s = aib_read_json(aib_agent_status_path()) ?: [];
    $last = aib_epoch_from_iso($s['last_seen'] ?? null);
    return [
        'online' => $last > 0 && $last >= time() - 45,
        'last_seen' => $s['last_seen'] ?? null,
        'agent_name' => $s['agent_name'] ?? null,
        'state' => $s['state'] ?? null,
        'model' => $s['model'] ?? null,
        'model_id' => $s['model_id'] ?? null,
        'model_ready' => !empty($s['model_ready']),
        'model_loading' => !empty($s['model_loading']),
        'models' => aib_public_models($s['models'] ?? []),
        'model_control' => aib_model_control(),
        'version' => $s['version'] ?? null,
        'active_message_id' => $s['active_message_id'] ?? null,
    ];
}

function aib_activity_event(array $event): array {
    $allowed = ['type','phase','label','detail','skill','status','ok','step','max_steps','error','at'];
    $out = [];
    foreach ($allowed as $key) {
        if (!array_key_exists($key, $event)) continue;
        $value = $event[$key];
        if (is_bool($value) || is_int($value) || is_float($value)) $out[$key] = $value;
        elseif (is_string($value)) $out[$key] = aib_clean_message($value, $key === 'detail' ? 1200 : 280);
    }
    if (empty($out['type'])) $out['type'] = 'progress';
    if (empty($out['at'])) $out['at'] = aib_now();
    return $out;
}

function aib_append_activity(string $id, array $event, string $agentName = 'LlamaForge', bool $visible = true): ?array {
    $config = aib_config();
    $limit = max(20, min(200, (int)($config['max_activity_events'] ?? 80)));
    return aib_update_message($id, function($row) use ($event, $agentName, $visible, $limit) {
        if (in_array(($row['status'] ?? ''), ['answered','cancelled'], true)) return $row;
        $row['status'] = 'processing';
        $row['claimed_at'] = aib_now();
        $row['agent_name'] = $agentName ?: 'LlamaForge';
        if ($visible) {
            $events = is_array($row['activity'] ?? null) ? $row['activity'] : [];
            $events[] = aib_activity_event($event);
            if (count($events) > $limit) $events = array_slice($events, -$limit);
            $row['activity'] = $events;
        }
        return $row;
    });
}

function aib_capability_token(): string {
    return 'cap_' . rtrim(strtr(base64_encode(random_bytes(32)), '+/', '-_'), '=');
}

function aib_capability_hash(string $cap): string {
    return hash('sha256', $cap);
}

function aib_requeue_expired_processing(): int {
    $config = aib_config();
    $lease = max(30, (int)($config['processing_lease_seconds'] ?? 90));
    $now = time();
    $count = 0;
    foreach (aib_all_messages() as $m) {
        if (($m['status'] ?? '') !== 'processing') continue;
        $claimed = aib_epoch_from_iso($m['claimed_at'] ?? null);
        if ($claimed > 0 && ($now - $claimed) < $lease) continue;
        $updated = aib_update_message((string)$m['id'], function($row) {
            if (($row['status'] ?? '') !== 'processing') return $row;
            $row['status'] = 'pending';
            $row['claimed_at'] = null;
            $row['agent_name'] = null;
            $row['reply_cap_hash'] = null;
            $row['reply_cap_expires_at'] = null;
            $row['reply_draft'] = null;
            $row['partial_answer'] = null;
            return $row;
        });
        if ($updated && ($updated['status'] ?? '') === 'pending') $count++;
    }
    return $count;
}

function aib_pending_messages(int $limit = 5, bool $claim = false): array {
    aib_requeue_expired_processing();
    $config = aib_config();
    $capTtl = max(120, (int)($config['reply_capability_ttl_seconds'] ?? 900));
    $eligible = [];
    foreach (aib_all_messages() as $m) {
        if (($m['status'] ?? 'pending') === 'pending') $eligible[] = $m;
        if (count($eligible) >= $limit) break;
    }
    if (!$claim) return $eligible;

    $claimed = [];
    foreach ($eligible as $m) {
        $cap = aib_capability_token();
        $capHash = aib_capability_hash($cap);
        $expires = gmdate('c', time() + $capTtl);
        $updated = aib_update_message((string)$m['id'], function($row) use ($capHash, $expires) {
            if (($row['status'] ?? 'pending') !== 'pending') return $row;
            $row['status'] = 'processing';
            $row['claimed_at'] = aib_now();
            $row['reply_cap_hash'] = $capHash;
            $row['reply_cap_expires_at'] = $expires;
            $row['reply_draft'] = null;
            $row['activity'] = [];
            return $row;
        });
        if ($updated && ($updated['status'] ?? '') === 'processing' && hash_equals((string)($updated['reply_cap_hash'] ?? ''), $capHash)) {
            $updated['_reply_capability'] = $cap;
            $claimed[] = $updated;
        }
    }
    return $claimed;
}

function aib_validate_reply_capability(array $row, string $cap): bool {
    if ($cap === '') return false;
    $stored = (string)($row['reply_cap_hash'] ?? '');
    if ($stored === '' || !hash_equals($stored, aib_capability_hash($cap))) return false;
    $expires = aib_epoch_from_iso($row['reply_cap_expires_at'] ?? null);
    return $expires > time();
}

function aib_invalidate_reply_capability(array &$row): void {
    $row['reply_cap_hash'] = null;
    $row['reply_cap_expires_at'] = null;
    $row['reply_draft'] = null;
}

function aib_public_message(array $m): array {
    return [
        'id' => $m['id'] ?? null,
        'session_id' => $m['session_id'] ?? null,
        'question' => $m['question'] ?? '',
        'answer' => $m['answer'] ?? null,
        'partial_answer' => $m['partial_answer'] ?? null,
        'status' => $m['status'] ?? 'pending',
        'agent_name' => $m['agent_name'] ?? null,
        'created_at' => $m['created_at'] ?? null,
        'claimed_at' => $m['claimed_at'] ?? null,
        'answered_at' => $m['answered_at'] ?? null,
        'activity' => is_array($m['activity'] ?? null) ? $m['activity'] : [],
        'requested_model_id' => $m['requested_model_id'] ?? null,
        'attachments' => aib_public_attachments($m['attachments'] ?? []),
        'cancel_requested' => !empty($m['cancel_requested']),
        'cancelled_at' => $m['cancelled_at'] ?? null,
    ];
}

function aib_clear_messages(): int {
    $count = 0;
    foreach (glob(AIB_MESSAGES . '/*.json') ?: [] as $file) {
        if (@unlink($file)) $count++;
    }
    return $count;
}


// -----------------------------------------------------------------------------
// Browser-scoped Calendar + File workspace
// -----------------------------------------------------------------------------
function aib_workspace_owner_valid(string $ownerHash): bool {
    return (bool)preg_match('/^[a-f0-9]{64}$/', $ownerHash);
}

function aib_workspace_root(string $ownerHash): string {
    if (!aib_workspace_owner_valid($ownerHash)) throw new RuntimeException('invalid_workspace_owner');
    return AIB_WORKSPACES . '/' . $ownerHash;
}

function aib_workspace_paths(string $ownerHash): array {
    $root = aib_workspace_root($ownerHash);
    return [
        'root'=>$root,
        'calendar'=>$root . '/calendar.json',
        'index'=>$root . '/files-index.json',
        'files'=>$root . '/files',
        'trash'=>$root . '/.trash',
        'state'=>$root . '/state.json',
    ];
}

function aib_workspace_init(string $ownerHash): array {
    $p = aib_workspace_paths($ownerHash);
    foreach ([$p['root'],$p['files'],$p['trash']] as $dir) {
        if (!is_dir($dir)) @mkdir($dir, 0750, true);
    }
    if (!is_file($p['calendar'])) aib_atomic_write($p['calendar'], json_encode(['events'=>[],'custom_holidays'=>[]], JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT));
    if (!is_file($p['index'])) aib_atomic_write($p['index'], json_encode(['items'=>[]], JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT));
    if (!is_file($p['state'])) aib_atomic_write($p['state'], json_encode(['revision'=>1,'updated_at'=>aib_now()], JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT));
    return $p;
}

function aib_workspace_revision(string $ownerHash): int {
    $p = aib_workspace_init($ownerHash);
    $s = aib_read_json($p['state']) ?: [];
    return max(1, (int)($s['revision'] ?? 1));
}

function aib_workspace_bump(string $ownerHash): int {
    $p = aib_workspace_init($ownerHash);
    $rev = aib_workspace_revision($ownerHash) + 1;
    aib_atomic_write($p['state'], json_encode(['revision'=>$rev,'updated_at'=>aib_now()], JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT));
    return $rev;
}

function aib_workspace_calendar(string $ownerHash): array {
    $p = aib_workspace_init($ownerHash);
    $db = aib_read_json($p['calendar']) ?: [];
    return [
        'events'=>is_array($db['events'] ?? null) ? array_values($db['events']) : [],
        'custom_holidays'=>is_array($db['custom_holidays'] ?? null) ? array_values($db['custom_holidays']) : [],
    ];
}

function aib_workspace_calendar_write(string $ownerHash, string $operation, array $data): array {
    $p = aib_workspace_init($ownerHash);
    $db = aib_workspace_calendar($ownerHash);
    $events = $db['events'];
    $op = strtolower(trim($operation));
    if ($op === 'create') {
        $title = aib_clean_message((string)($data['title'] ?? ''), 300);
        $start = aib_clean_message((string)($data['start'] ?? ''), 80);
        $end = aib_clean_message((string)($data['end'] ?? ''), 80);
        if ($title === '' || $start === '' || strtotime($start) === false) throw new RuntimeException('title_and_start_required');
        if ($end === '' || strtotime($end) === false) $end = gmdate('c', strtotime($start) + 3600);
        $row = [
            'id'=>'evt_' . bin2hex(random_bytes(8)),
            'title'=>$title,
            'start'=>$start,
            'end'=>$end,
            'all_day'=>!empty($data['all_day']),
            'location'=>aib_clean_message((string)($data['location'] ?? ''), 500),
            'notes'=>aib_clean_message((string)($data['notes'] ?? ''), 5000),
            'tags'=>array_values(array_slice(array_map(fn($x)=>aib_clean_message((string)$x,80), is_array($data['tags'] ?? null)?$data['tags']:[]),0,20)),
            'reminders'=>array_values(array_slice(array_map('intval', is_array($data['reminders'] ?? null)?$data['reminders']:[]),0,10)),
            'status'=>'active','created_at'=>aib_now(),'updated_at'=>aib_now(),
        ];
        $events[] = $row;
    } else {
        $id = aib_clean_message((string)($data['id'] ?? ''), 80);
        $idx = null;
        foreach ($events as $i=>$ev) if (is_array($ev) && (string)($ev['id'] ?? '') === $id) { $idx=$i; break; }
        if ($idx === null) throw new RuntimeException('event_not_found');
        $row = is_array($events[$idx]) ? $events[$idx] : [];
        if ($op === 'delete') {
            array_splice($events, $idx, 1);
            $row = ['id'=>$id,'deleted'=>true];
        } elseif ($op === 'cancel') {
            $row['status']='cancelled'; $row['updated_at']=aib_now(); $events[$idx]=$row;
        } elseif ($op === 'update') {
            foreach (['title'=>300,'start'=>80,'end'=>80,'location'=>500,'notes'=>5000] as $key=>$max) {
                if (array_key_exists($key,$data)) $row[$key]=aib_clean_message((string)$data[$key],$max);
            }
            if (array_key_exists('all_day',$data)) $row['all_day']=!empty($data['all_day']);
            if (array_key_exists('tags',$data)) $row['tags']=array_values(array_slice(array_map(fn($x)=>aib_clean_message((string)$x,80), is_array($data['tags'])?$data['tags']:[]),0,20));
            if (array_key_exists('reminders',$data)) $row['reminders']=array_values(array_slice(array_map('intval', is_array($data['reminders'])?$data['reminders']:[]),0,10));
            $row['updated_at']=aib_now(); $events[$idx]=$row;
        } else throw new RuntimeException('unsupported_calendar_operation');
    }
    $db['events']=array_values($events);
    if (!aib_atomic_write($p['calendar'], json_encode($db, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT))) throw new RuntimeException('calendar_storage_error');
    $rev = aib_workspace_bump($ownerHash);
    return ['result'=>$row,'revision'=>$rev];
}

function aib_workspace_clean_rel(string $path): string {
    $path = str_replace('\\','/', trim($path));
    $parts=[];
    foreach (explode('/',$path) as $part) {
        $part=trim($part);
        if ($part==='' || $part==='.' || $part==='..') continue;
        $part=preg_replace('/[\\x00-\\x1f\\x7f]+/u','',$part) ?? '';
        if ($part!=='') $parts[]=$part;
    }
    return implode('/',$parts);
}

function aib_workspace_safe_name(string $name, string $fallback='file'): string {
    $name = basename(str_replace('\\','/',$name));
    $name = preg_replace('/[\\x00-\\x1f\\x7f\\/]+/u',' ', $name) ?? '';
    $name = trim($name);
    return $name !== '' ? aib_clean_message($name,180) : $fallback;
}

function aib_workspace_index(string $ownerHash): array {
    $p=aib_workspace_init($ownerHash);
    $idx=aib_read_json($p['index']) ?: [];
    $items=is_array($idx['items'] ?? null)?$idx['items']:[];
    return ['items'=>$items];
}

function aib_workspace_save_index(string $ownerHash, array $idx): bool {
    $p=aib_workspace_init($ownerHash);
    if (!is_array($idx['items'] ?? null)) $idx=['items'=>[]];
    return aib_atomic_write($p['index'], json_encode($idx,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT));
}

function aib_workspace_register_file(string $ownerHash, string $fullPath, string $rel, string $id='', array $extra=[]): array {
    $idx=aib_workspace_index($ownerHash);
    $id=$id!==''?$id:'file_' . bin2hex(random_bytes(8));
    $row=array_merge([
        'id'=>$id,'name'=>basename($fullPath),'path'=>$rel,'size'=>(int)@filesize($fullPath),
        'mime'=>function_exists('mime_content_type')?((string)@mime_content_type($fullPath) ?: 'application/octet-stream'):'application/octet-stream',
        'description'=>'','tags'=>[],'source'=>'web','status'=>'active','updated_at'=>aib_now(),
    ],$extra);
    $idx['items'][$id]=$row;
    aib_workspace_save_index($ownerHash,$idx);
    return $row;
}

function aib_workspace_files_list(string $ownerHash, string $folder='', string $query=''): array {
    $p=aib_workspace_init($ownerHash);
    $idx=aib_workspace_index($ownerHash);
    $query=trim($query);
    if ($query!=='') {
        $q=function_exists('mb_strtolower')?mb_strtolower($query,'UTF-8'):strtolower($query);
        $out=[];
        foreach ($idx['items'] as $row) {
            if (!is_array($row) || ($row['status'] ?? '')==='trash') continue;
            $hay=(string)($row['name'] ?? '').' '.(string)($row['path'] ?? '').' '.(string)($row['description'] ?? '').' '.implode(' ',is_array($row['tags'] ?? null)?$row['tags']:[]);
            $hay=function_exists('mb_strtolower')?mb_strtolower($hay,'UTF-8'):strtolower($hay);
            if (strpos($hay,$q)!==false) $out[]=['kind'=>'file']+$row;
            if (count($out)>=100) break;
        }
        return ['folder'=>'','items'=>$out];
    }
    $folder=aib_workspace_clean_rel($folder);
    $root=$p['files'].($folder!==''?'/'.$folder:'');
    if (!is_dir($root)) @mkdir($root,0750,true);
    $byPath=[];
    foreach ($idx['items'] as $row) if (is_array($row) && ($row['status'] ?? '')!=='trash') $byPath[(string)($row['path'] ?? '')]=$row;
    $items=[];
    foreach (scandir($root) ?: [] as $name) {
        if ($name==='.'||$name==='..'||strpos($name,'.') === 0) continue;
        $full=$root.'/'.$name; $rel=ltrim(($folder!==''?$folder.'/':'').$name,'/');
        if (is_dir($full)) $items[]=['kind'=>'folder','name'=>$name,'path'=>$rel];
        elseif (is_file($full)) {
            $row=$byPath[$rel] ?? aib_workspace_register_file($ownerHash,$full,$rel);
            $items[]=['kind'=>'file']+$row;
        }
    }
    usort($items,fn($a,$b)=>(($a['kind']??'')===($b['kind']??''))?strnatcasecmp((string)($a['name']??''),(string)($b['name']??'')):(($a['kind']??'')==='folder'?-1:1));
    return ['folder'=>$folder,'items'=>$items];
}

function aib_workspace_decode_data_url(string $dataUrl): array {
    if (!preg_match('#^data:([^;,]+)?(?:;charset=[^;,]+)?;base64,(.*)$#is',$dataUrl,$m)) throw new RuntimeException('invalid_data_url');
    $data=base64_decode($m[2],true);
    if ($data===false) throw new RuntimeException('invalid_base64');
    return [$m[1] ?: 'application/octet-stream',$data];
}

function aib_workspace_upload(string $ownerHash, array $data): array {
    $p=aib_workspace_init($ownerHash);
    $folder=aib_workspace_clean_rel((string)($data['folder'] ?? ''));
    $name=aib_workspace_safe_name((string)($data['name'] ?? 'file'));
    [$mime,$bytes]=aib_workspace_decode_data_url((string)($data['data_url'] ?? ''));
    if (strlen($bytes)>50*1024*1024) throw new RuntimeException('file_too_large');
    $dir=$p['files'].($folder!==''?'/'.$folder:''); if(!is_dir($dir))@mkdir($dir,0750,true);
    $target=$dir.'/'.$name; $pi=pathinfo($name);$stem=$pi['filename']??'file';$ext=isset($pi['extension'])?'.'.$pi['extension']:'';$n=2;
    while(file_exists($target)){$name=$stem.' ('.$n.')'.$ext;$target=$dir.'/'.$name;$n++;}
    if(file_put_contents($target,$bytes,LOCK_EX)===false)throw new RuntimeException('upload_failed');
    $rel=ltrim(($folder!==''?$folder.'/':'').$name,'/');
    $row=aib_workspace_register_file($ownerHash,$target,$rel,'',['mime'=>$mime,'description'=>aib_clean_message((string)($data['description']??''),1000),'tags'=>array_values(array_slice(is_array($data['tags']??null)?$data['tags']:[],0,30))]);
    $rev=aib_workspace_bump($ownerHash);
    return ['file'=>$row,'revision'=>$rev];
}

function aib_workspace_resolve_file(string $ownerHash, string $id): array {
    $p=aib_workspace_init($ownerHash);$idx=aib_workspace_index($ownerHash);$row=$idx['items'][$id]??null;
    if(!is_array($row))throw new RuntimeException('file_not_found');
    $root=($row['status']??'')==='trash'?$p['trash']:$p['files'];
    $rel=aib_workspace_clean_rel((string)($row['path']??$row['name']??''));$full=$root.($rel!==''?'/'.$rel:'');
    if(!is_file($full))throw new RuntimeException('file_missing');
    return [$row,$full,$idx];
}

function aib_workspace_file_action(string $ownerHash, string $operation, array $data): array {
    $p=aib_workspace_init($ownerHash);$op=strtolower(trim($operation));
    if($op==='mkdir'){
        $folder=aib_workspace_clean_rel((string)($data['folder']??''));$name=aib_workspace_safe_name((string)($data['name']??''),'');if($name==='')throw new RuntimeException('folder_name_required');
        $path=$p['files'].($folder!==''?'/'.$folder:'').'/'.$name;if(!is_dir($path)&&!mkdir($path,0750,true))throw new RuntimeException('mkdir_failed');
        $rev=aib_workspace_bump($ownerHash);return ['result'=>['created'=>true,'kind'=>'folder','path'=>ltrim(($folder!==''?$folder.'/':'').$name,'/')],'revision'=>$rev];
    }
    $id=aib_clean_message((string)($data['id']??''),100);[$row,$full,$idx]=aib_workspace_resolve_file($ownerHash,$id);
    if($op==='trash'){
        $dest=$p['trash'].'/'.basename($full);$n=2;while(file_exists($dest)){$pi=pathinfo($dest);$dest=$p['trash'].'/'.($pi['filename']??'file').'_'.$n.(isset($pi['extension'])?'.'.$pi['extension']:'');$n++;}
        if(!@rename($full,$dest))throw new RuntimeException('move_failed');$row['original_path']=$row['path'];$row['path']=basename($dest);$row['status']='trash';
    } elseif($op==='restore'){
        if(($row['status']??'')!=='trash')throw new RuntimeException('file_not_in_trash');$rel=aib_workspace_clean_rel((string)($row['original_path']??$row['name']));$dest=$p['files'].'/'.$rel;@mkdir(dirname($dest),0750,true);if(file_exists($dest))$dest=dirname($dest).'/restored_'.basename($dest);if(!@rename($full,$dest))throw new RuntimeException('restore_failed');$row['path']=ltrim(str_replace($p['files'],'',$dest),'/');$row['name']=basename($dest);$row['status']='active';
    } elseif($op==='delete'){
        @unlink($full);unset($idx['items'][$id]);aib_workspace_save_index($ownerHash,$idx);$rev=aib_workspace_bump($ownerHash);return ['result'=>['deleted'=>true,'id'=>$id],'revision'=>$rev];
    } elseif(in_array($op,['move','rename'],true)){
        $folder=aib_workspace_clean_rel((string)($data['folder']??dirname((string)($row['path']??''))));if($folder==='.')$folder='';$name=aib_workspace_safe_name((string)($data['name']??$row['name']??basename($full)));
        $dest=$p['files'].($folder!==''?'/'.$folder:'').'/'.$name;@mkdir(dirname($dest),0750,true);if(file_exists($dest)&&realpath($dest)!==realpath($full))throw new RuntimeException('destination_exists');if(!@rename($full,$dest))throw new RuntimeException('move_failed');$row['path']=ltrim(($folder!==''?$folder.'/':'').$name,'/');$row['name']=$name;$row['status']='active';
    } else throw new RuntimeException('unsupported_file_operation');
    $row['updated_at']=aib_now();$idx['items'][$id]=$row;aib_workspace_save_index($ownerHash,$idx);$rev=aib_workspace_bump($ownerHash);return ['result'=>$row,'revision'=>$rev];
}

function aib_workspace_export_snapshot(string $ownerHash): array {
    $p=aib_workspace_init($ownerHash);$idx=aib_workspace_index($ownerHash);$folders=[];$files=[];$total=0;
    $it=new RecursiveIteratorIterator(new RecursiveDirectoryIterator($p['files'],FilesystemIterator::SKIP_DOTS),RecursiveIteratorIterator::SELF_FIRST);
    foreach($it as $node){if($node->isDir()){$rel=ltrim(str_replace('\\','/',substr($node->getPathname(),strlen($p['files']))),'/');if($rel!=='')$folders[]=$rel;}}
    foreach($idx['items'] as $id=>$row){if(!is_array($row))continue;$root=($row['status']??'')==='trash'?$p['trash']:$p['files'];$rel=aib_workspace_clean_rel((string)($row['path']??$row['name']??''));$full=$root.($rel!==''?'/'.$rel:'');if(!is_file($full))continue;$bytes=file_get_contents($full);if($bytes===false)continue;$total+=strlen($bytes);if($total>64*1024*1024)throw new RuntimeException('workspace_snapshot_too_large');$files[]=['id'=>(string)$id,'status'=>(string)($row['status']??'active'),'path'=>(string)($row['path']??basename($full)),'data_base64'=>base64_encode($bytes)];}
    return ['format'=>'llamaforge-workspace-v1','calendar'=>aib_workspace_calendar($ownerHash),'files'=>['index'=>$idx,'folders'=>array_values(array_unique($folders)),'files'=>$files]];
}

function aib_workspace_import_snapshot(string $ownerHash, array $snapshot): int {
    $p=aib_workspace_init($ownerHash);$cal=is_array($snapshot['calendar']??null)?$snapshot['calendar']:['events'=>[],'custom_holidays'=>[]];$fileSnap=is_array($snapshot['files']??null)?$snapshot['files']:[];
    aib_atomic_write($p['calendar'],json_encode(['events'=>array_values(is_array($cal['events']??null)?$cal['events']:[]),'custom_holidays'=>array_values(is_array($cal['custom_holidays']??null)?$cal['custom_holidays']:[])],JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT));
    $rm=function($dir)use(&$rm){if(!is_dir($dir))return;foreach(scandir($dir)?:[] as $n){if($n==='.'||$n==='..')continue;$f=$dir.'/'.$n;if(is_dir($f)){$rm($f);@rmdir($f);}else @unlink($f);}};$rm($p['files']);$rm($p['trash']);@mkdir($p['files'],0750,true);@mkdir($p['trash'],0750,true);
    foreach(is_array($fileSnap['folders']??null)?$fileSnap['folders']:[] as $folder){$rel=aib_workspace_clean_rel((string)$folder);if($rel!=='')@mkdir($p['files'].'/'.$rel,0750,true);}
    $total=0;foreach(is_array($fileSnap['files']??null)?$fileSnap['files']:[] as $blob){if(!is_array($blob))continue;$bytes=base64_decode((string)($blob['data_base64']??''),true);if($bytes===false)continue;$total+=strlen($bytes);if($total>64*1024*1024)throw new RuntimeException('workspace_snapshot_too_large');$root=((string)($blob['status']??''))==='trash'?$p['trash']:$p['files'];$rel=aib_workspace_clean_rel((string)($blob['path']??'file'));$target=$root.'/'.$rel;@mkdir(dirname($target),0750,true);file_put_contents($target,$bytes,LOCK_EX);}
    $idx=is_array($fileSnap['index']??null)?$fileSnap['index']:['items'=>[]];if(!is_array($idx['items']??null))$idx=['items'=>[]];aib_workspace_save_index($ownerHash,$idx);
    return aib_workspace_bump($ownerHash);
}

function aib_workspace_owners(): array {
    if(!is_dir(AIB_WORKSPACES))return [];$out=[];
    foreach(scandir(AIB_WORKSPACES)?:[] as $name){if(!aib_workspace_owner_valid($name))continue;$out[]=['owner_hash'=>$name,'revision'=>aib_workspace_revision($name)];}
    return $out;
}
