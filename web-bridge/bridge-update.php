<?php
require __DIR__ . '/lib/bootstrap.php';
header('X-Robots-Tag: noindex, nofollow, noarchive');

if (!aib_agent_auth()) {
    aib_json_response(['ok'=>false,'error'=>'invalid_or_missing_token'], 401);
}

const AIB_UPDATER_VERSION = '2.0-atomic-stable-identity';
const AIB_MAX_UPDATE_BYTES = 12_000_000;
const AIB_MAX_PACKAGE_FILES = 700;
const AIB_MAX_PACKAGE_UNCOMPRESSED = 32_000_000;

function aib_update_root(): string { return AIB_ROOT . '/.aib-updates'; }
function aib_update_backups(): string { return aib_update_root() . '/backups'; }
function aib_update_stages(): string { return aib_update_root() . '/stages'; }

function aib_update_lock() {
    aib_update_mkdir(aib_update_root());
    $fh = @fopen(aib_update_root() . '/update.lock', 'c+');
    if (!$fh) aib_json_response(['ok'=>false,'error'=>'could_not_open_update_lock'], 500);
    if (!@flock($fh, LOCK_EX | LOCK_NB)) {
        @fclose($fh);
        aib_json_response(['ok'=>false,'error'=>'bridge_update_already_running'], 409);
    }
    @ftruncate($fh, 0);
    @fwrite($fh, (string)getmypid() . ' ' . aib_now());
    @fflush($fh);
    return $fh;
}

function aib_update_mkdir(string $path): void {
    if (!is_dir($path) && !@mkdir($path, 0750, true) && !is_dir($path)) {
        throw new RuntimeException('Could not create update directory');
    }
}

function aib_update_rm(string $path): void {
    if (is_link($path) || is_file($path)) { @unlink($path); return; }
    if (!is_dir($path)) return;
    $items = scandir($path);
    if ($items !== false) foreach ($items as $name) {
        if ($name === '.' || $name === '..') continue;
        aib_update_rm($path . '/' . $name);
    }
    @rmdir($path);
}

function aib_update_copy(string $src, string $dst): void {
    if (is_link($src)) throw new RuntimeException('Symlinks are not allowed in bridge backups/packages');
    if (is_file($src)) {
        aib_update_mkdir(dirname($dst));
        if (!@copy($src, $dst)) throw new RuntimeException('Could not copy ' . basename($src));
        return;
    }
    if (!is_dir($src)) return;
    aib_update_mkdir($dst);
    $items = scandir($src);
    if ($items === false) throw new RuntimeException('Could not read directory');
    foreach ($items as $name) {
        if ($name === '.' || $name === '..') continue;
        aib_update_copy($src . '/' . $name, $dst . '/' . $name);
    }
}

function aib_update_atomic_copy_file(string $src, string $dst): void {
    if (is_link($src)) throw new RuntimeException('Symlinks are not allowed in bridge packages');
    if (!is_file($src)) return;
    aib_update_mkdir(dirname($dst));
    $suffix = '.aib-new-' . substr(bin2hex(random_bytes(6)), 0, 12);
    $tmp = $dst . $suffix;
    if (!@copy($src, $tmp)) throw new RuntimeException('Could not stage ' . basename($src));
    @chmod($tmp, 0644);

    // On normal Linux hosting rename() replaces a file atomically, so live
    // requests always see either the complete old file or the complete new file.
    if (@rename($tmp, $dst)) return;

    // Portable fallback for hosts/filesystems that do not replace an existing
    // file with rename(). Keep the old file beside the target and restore it if
    // activation fails, instead of deleting the whole Bridge first.
    $old = $dst . '.aib-old-' . substr(bin2hex(random_bytes(6)), 0, 12);
    $hadOld = is_file($dst);
    if ($hadOld && !@rename($dst, $old)) {
        @unlink($tmp);
        throw new RuntimeException('Could not prepare atomic replacement for ' . basename($dst));
    }
    if (!@rename($tmp, $dst)) {
        if ($hadOld) @rename($old, $dst);
        @unlink($tmp);
        throw new RuntimeException('Could not activate ' . basename($dst));
    }
    if ($hadOld) @unlink($old);
}

function aib_update_atomic_copy_tree(string $src, string $dst): void {
    if (is_link($src)) throw new RuntimeException('Symlinks are not allowed in bridge packages');
    if (is_file($src)) { aib_update_atomic_copy_file($src, $dst); return; }
    if (!is_dir($src)) return;
    aib_update_mkdir($dst);
    $items = scandir($src);
    if ($items === false) throw new RuntimeException('Could not read staged directory');
    foreach ($items as $name) {
        if ($name === '.' || $name === '..') continue;
        aib_update_atomic_copy_tree($src . '/' . $name, $dst . '/' . $name);
    }
}

function aib_update_read_config_file(): array {
    if (!is_file(AIB_CONFIG_FILE)) return [];
    try {
        $cfg = require AIB_CONFIG_FILE;
        return is_array($cfg) ? $cfg : [];
    } catch (Throwable $e) {
        return [];
    }
}

function aib_update_identity_snapshot(): array {
    $cfg = aib_update_read_config_file();
    $agent = trim((string)($cfg['agent_token'] ?? ''));
    $owner = trim((string)($cfg['owner_key'] ?? ''));
    if ($agent === '' || $owner === '') throw new RuntimeException('Stable Bridge credentials are missing; update aborted before changing code');
    return [
        'agent_token'=>$agent,
        'owner_key'=>$owner,
        'agent_token_id'=>substr(hash('sha256', $agent), 0, 16),
        'owner_key_id'=>substr(hash('sha256', $owner), 0, 16),
    ];
}

function aib_update_restore_identity(array $identity, string $version): void {
    $cfg = aib_update_read_config_file();
    if (!$cfg) $cfg = aib_default_config(false);
    $cfg['agent_token'] = (string)$identity['agent_token'];
    $cfg['owner_key'] = (string)$identity['owner_key'];
    $cfg['version'] = $version;
    if (!aib_write_config($cfg)) throw new RuntimeException('Could not persist stable Bridge credentials');
    $verify = aib_update_read_config_file();
    if (!hash_equals((string)$identity['agent_token'], (string)($verify['agent_token'] ?? '')) ||
        !hash_equals((string)$identity['owner_key'], (string)($verify['owner_key'] ?? ''))) {
        throw new RuntimeException('Credential preservation verification failed');
    }
}

function aib_update_identity_public(array $identity): array {
    return [
        'agent_token_id'=>(string)($identity['agent_token_id'] ?? ''),
        'owner_key_id'=>(string)($identity['owner_key_id'] ?? ''),
        'credentials_preserved'=>true,
    ];
}

function aib_update_managed_top(): array {
    return [
        'assets','lib','agent-action.php','agent-stream.php','agent.php','api.php',
        'bridge-update.php','connect.php','index.php','manage.php','.htaccess',
        'CHANGELOG.txt','PROFESSIONAL_UI_FA.md','README.md','SECURITY.md','START-HERE.txt','VERSION'
    ];
}

function aib_update_backup_list(): array {
    $dir = aib_update_backups();
    if (!is_dir($dir)) return [];
    $rows = [];
    foreach (glob($dir . '/*/meta.json') ?: [] as $metaFile) {
        $row = aib_read_json($metaFile);
        if (!is_array($row)) continue;
        $row['id'] = basename(dirname($metaFile));
        $rows[] = $row;
    }
    usort($rows, fn($a,$b)=>strcmp((string)($b['created_at'] ?? ''), (string)($a['created_at'] ?? '')));
    return array_slice($rows, 0, 20);
}

function aib_update_prune_backups(int $keep = 6): void {
    $rows = aib_update_backup_list();
    foreach (array_slice($rows, max(1, $keep)) as $row) {
        $id = preg_replace('/[^A-Za-z0-9._-]/', '', (string)($row['id'] ?? ''));
        if ($id !== '') aib_update_rm(aib_update_backups() . '/' . $id);
    }
}

function aib_update_create_backup(string $targetVersion): array {
    aib_update_mkdir(aib_update_backups());
    $id = gmdate('Ymd-His') . '-' . substr(bin2hex(random_bytes(5)), 0, 10);
    $dst = aib_update_backups() . '/' . $id;
    aib_update_mkdir($dst);
    foreach (aib_update_managed_top() as $name) {
        $src = AIB_ROOT . '/' . $name;
        if (file_exists($src) || is_link($src)) aib_update_copy($src, $dst . '/code/' . $name);
    }
    foreach (['.htaccess','index.php','config.example.php','.gitignore'] as $name) {
        $src = AIB_DATA . '/' . $name;
        if (is_file($src)) aib_update_copy($src, $dst . '/data-static/' . $name);
    }
    $cfg = aib_config();
    $meta = [
        'id'=>$id,
        'created_at'=>aib_now(),
        'from_version'=>(string)($cfg['version'] ?? 'unknown'),
        'target_version'=>$targetVersion,
    ];
    if (!aib_atomic_write($dst . '/meta.json', json_encode($meta, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT))) {
        throw new RuntimeException('Could not write backup metadata');
    }
    aib_update_prune_backups(6);
    return $meta;
}

function aib_update_deploy_stage(string $stage): void {
    // ZIP -> staging -> checksum verification happens before this function.
    // Deploy support files first, public entry points next, updater near-last,
    // and VERSION last. No live Bridge file is ever bulk-deleted.
    foreach (['assets','lib'] as $name) {
        $src = $stage . '/' . $name;
        if (is_dir($src)) aib_update_atomic_copy_tree($src, AIB_ROOT . '/' . $name);
    }
    foreach (['agent-action.php','agent-stream.php','agent.php','api.php','connect.php','index.php','manage.php',
              'CHANGELOG.txt','PROFESSIONAL_UI_FA.md','README.md','SECURITY.md','START-HERE.txt'] as $name) {
        $src = $stage . '/' . $name;
        if (is_file($src)) aib_update_atomic_copy_file($src, AIB_ROOT . '/' . $name);
    }
    foreach (['.htaccess','index.php','config.example.php','.gitignore'] as $name) {
        $src = $stage . '/data/' . $name;
        if (is_file($src)) aib_update_atomic_copy_file($src, AIB_DATA . '/' . $name);
    }
    foreach (['.htaccess','bridge-update.php','VERSION'] as $name) {
        $src = $stage . '/' . $name;
        if (is_file($src)) aib_update_atomic_copy_file($src, AIB_ROOT . '/' . $name);
    }
}

function aib_update_restore_backup_code(string $id, ?array $identity = null): void {
    $id = preg_replace('/[^A-Za-z0-9._-]/','',$id);
    if ($id === '') throw new RuntimeException('Invalid backup id');
    $src = aib_update_backups() . '/' . $id;
    $meta = aib_read_json($src . '/meta.json');
    if (!is_array($meta) || !is_dir($src . '/code')) throw new RuntimeException('Backup not found');
    $identity = $identity ?: aib_update_identity_snapshot();
    foreach (aib_update_managed_top() as $name) {
        $p = $src . '/code/' . $name;
        if (is_file($p)) aib_update_atomic_copy_file($p, AIB_ROOT . '/' . $name);
        elseif (is_dir($p)) aib_update_atomic_copy_tree($p, AIB_ROOT . '/' . $name);
    }
    foreach (['.htaccess','index.php','config.example.php','.gitignore'] as $name) {
        $p = $src . '/data-static/' . $name;
        if (is_file($p)) aib_update_atomic_copy_file($p, AIB_DATA . '/' . $name);
    }
    $version = (string)($meta['from_version'] ?? 'unknown');
    aib_update_restore_identity($identity, $version);
}

function aib_update_status(): array {
    $cfg = aib_config();
    $identity = aib_update_identity_snapshot();
    return [
        'ok'=>true,
        'updater_version'=>AIB_UPDATER_VERSION,
        'bridge_version'=>(string)($cfg['version'] ?? 'unknown'),
        'update_pipeline'=>'zip_upload -> stage_extract -> checksum_verify -> atomic_activate',
        'credentials_policy'=>'persistent_never_rotate_on_update',
        'agent_token_id'=>$identity['agent_token_id'],
        'owner_key_id'=>$identity['owner_key_id'],
        'backups'=>aib_update_backup_list(),
        'max_package_bytes'=>AIB_MAX_UPDATE_BYTES,
        'zip_available'=>(class_exists('ZipArchive') || class_exists('PharData')),
        'zip_engine'=>class_exists('ZipArchive') ? 'ZipArchive' : (class_exists('PharData') ? 'PharData' : 'none'),
        'server_time'=>aib_now(),
    ];
}

function aib_update_extract_zip(string $zipPath, string $stage): array {
    aib_update_mkdir($stage);
    $total = 0;
    $count = 0;
    $writeEntry = function(string $name, string $bytes, int $size) use ($stage, &$total, &$count): void {
        $name = str_replace('\\', '/', $name);
        if ($name === '' || strpos($name, "\0") !== false || $name[0] === '/' || preg_match('#(^|/)\.\.(/|$)#', $name)) {
            throw new RuntimeException('Unsafe ZIP path');
        }
        $count++;
        if ($count > AIB_MAX_PACKAGE_FILES) throw new RuntimeException('Invalid package file count');
        $total += max(0, $size);
        if ($total > AIB_MAX_PACKAGE_UNCOMPRESSED) throw new RuntimeException('Package is too large after extraction');
        $target = $stage . '/' . $name;
        aib_update_mkdir(dirname($target));
        if (@file_put_contents($target, $bytes, LOCK_EX) === false) throw new RuntimeException('Could not write staged file');
    };

    if (class_exists('ZipArchive')) {
        $zip = new ZipArchive();
        if ($zip->open($zipPath) !== true) throw new RuntimeException('Could not open update ZIP');
        try {
            if ($zip->numFiles < 1 || $zip->numFiles > AIB_MAX_PACKAGE_FILES) throw new RuntimeException('Invalid package file count');
            for ($i=0; $i<$zip->numFiles; $i++) {
                $stat = $zip->statIndex($i);
                if (!is_array($stat)) throw new RuntimeException('Invalid ZIP entry');
                $name = str_replace('\\', '/', (string)($stat['name'] ?? ''));
                if (substr($name, -1) === '/') continue;
                $bytes = $zip->getFromIndex($i);
                if ($bytes === false) throw new RuntimeException('Could not read ZIP entry');
                $writeEntry($name, $bytes, (int)($stat['size'] ?? strlen($bytes)));
            }
        } finally {
            $zip->close();
        }
    } elseif (class_exists('PharData')) {
        try {
            $phar = new PharData($zipPath);
            $it = new RecursiveIteratorIterator($phar, RecursiveIteratorIterator::LEAVES_ONLY);
            foreach ($it as $file) {
                if (!$file->isFile()) continue;
                $name = str_replace('\\', '/', (string)$it->getSubPathName());
                $bytes = @file_get_contents($file->getPathname());
                if ($bytes === false) throw new RuntimeException('Could not read ZIP entry through PharData');
                $writeEntry($name, $bytes, (int)$file->getSize());
            }
            if ($count < 1) throw new RuntimeException('Update ZIP is empty');
        } catch (Throwable $e) {
            throw new RuntimeException('Could not open update ZIP with PharData: ' . $e->getMessage());
        }
    } else {
        throw new RuntimeException('This PHP installation has neither ZipArchive nor PharData support');
    }

    $manifest = aib_read_json($stage . '/bridge-manifest.json');
    if (!is_array($manifest) || ($manifest['format'] ?? '') !== 'llamaforge-web-bridge-update-v1') {
        throw new RuntimeException('Update package manifest is missing or incompatible');
    }
    $version = trim((string)($manifest['version'] ?? ''));
    if ($version === '') throw new RuntimeException('Update package has no version');
    $files = $manifest['files'] ?? [];
    if (!is_array($files)) throw new RuntimeException('Invalid update manifest');
    foreach ($files as $rel=>$expected) {
        $rel = str_replace('\\','/',(string)$rel);
        if ($rel === '' || preg_match('#(^|/)\.\.(/|$)#',$rel)) throw new RuntimeException('Unsafe manifest path');
        $path = $stage . '/' . $rel;
        if (!is_file($path)) throw new RuntimeException('Package file missing: ' . $rel);
        if (!hash_equals(strtolower((string)$expected), strtolower(hash_file('sha256',$path)))) throw new RuntimeException('Package checksum mismatch: ' . $rel);
    }
    return ['manifest'=>$manifest,'version'=>$version,'files'=>count($files),'zip_engine'=>class_exists('ZipArchive')?'ZipArchive':'PharData'];
}

function aib_update_install_raw(): void {
    $updateLock = aib_update_lock();
    $length = (int)($_SERVER['CONTENT_LENGTH'] ?? 0);
    if ($length <= 0 || $length > AIB_MAX_UPDATE_BYTES) aib_json_response(['ok'=>false,'error'=>'invalid_package_size'], 413);
    aib_update_mkdir(aib_update_root());
    aib_update_mkdir(aib_update_stages());
    $tmp = tempnam(aib_update_root(), 'upload-');
    if ($tmp === false) aib_json_response(['ok'=>false,'error'=>'could_not_create_temp_file'], 500);
    $in = fopen('php://input','rb'); $out = fopen($tmp,'wb');
    $copied = ($in && $out) ? stream_copy_to_stream($in,$out,AIB_MAX_UPDATE_BYTES+1) : false;
    if (is_resource($in)) fclose($in); if (is_resource($out)) fclose($out);
    if ($copied === false || $copied <= 0 || $copied > AIB_MAX_UPDATE_BYTES) { @unlink($tmp); aib_json_response(['ok'=>false,'error'=>'could_not_receive_package'],400); }
    $expected = strtolower(trim((string)($_SERVER['HTTP_X_AIB_PACKAGE_SHA256'] ?? '')));
    $actual = strtolower(hash_file('sha256',$tmp));
    if ($expected !== '' && !hash_equals($expected,$actual)) { @unlink($tmp); aib_json_response(['ok'=>false,'error'=>'package_sha256_mismatch'],400); }
    // PharData recognizes ZIPs by filename extension. ZipArchive does not care.
    if (!class_exists('ZipArchive') && class_exists('PharData')) {
        $zipTmp = $tmp . '.zip';
        if (!@rename($tmp, $zipTmp)) { @unlink($tmp); aib_json_response(['ok'=>false,'error'=>'could_not_prepare_zip_for_phardata'],500); }
        $tmp = $zipTmp;
    }
    $stageId = 'stage-' . gmdate('Ymd-His') . '-' . substr(bin2hex(random_bytes(4)),0,8);
    $stage = aib_update_stages() . '/' . $stageId;
    $backup = null;
    $identity = aib_update_identity_snapshot();
    try {
        $info = aib_update_extract_zip($tmp,$stage);
        $backup = aib_update_create_backup((string)$info['version']);
        aib_update_deploy_stage($stage);
        aib_update_restore_identity($identity, (string)$info['version']);
        @unlink($tmp); aib_update_rm($stage);
        aib_json_response(array_merge([
            'ok'=>true,'installed_version'=>$info['version'],'backup'=>$backup,'files'=>$info['files'],
            'package_sha256'=>$actual,'restart_required'=>false,'activation'=>'atomic_file_replace'
        ], aib_update_identity_public($identity)));
    } catch (Throwable $e) {
        $rollbackError = '';
        if (is_array($backup) && !empty($backup['id'])) {
            try { aib_update_restore_backup_code((string)$backup['id'], $identity); }
            catch (Throwable $rb) { $rollbackError = $rb->getMessage(); }
        }
        @unlink($tmp); aib_update_rm($stage);
        aib_json_response(['ok'=>false,'error'=>$e->getMessage(),'automatic_rollback'=>($rollbackError===''),'rollback_error'=>$rollbackError],500);
    }
}

function aib_update_rollback(string $id): void {
    $updateLock = aib_update_lock();
    $id = preg_replace('/[^A-Za-z0-9._-]/','',$id);
    if ($id === '') aib_json_response(['ok'=>false,'error'=>'backup_id_required'],400);
    $src = aib_update_backups() . '/' . $id;
    $meta = aib_read_json($src . '/meta.json');
    if (!is_array($meta) || !is_dir($src . '/code')) aib_json_response(['ok'=>false,'error'=>'backup_not_found'],404);
    try {
        // Make a safety backup of the currently installed version before rollback.
        $identity = aib_update_identity_snapshot();
        $safety = aib_update_create_backup((string)($meta['from_version'] ?? 'rollback'));
        aib_update_restore_backup_code($id, $identity);
        $rolled = (string)($meta['from_version'] ?? 'unknown');
        aib_json_response(array_merge(['ok'=>true,'rolled_back_to'=>$rolled,'backup_id'=>$id,'safety_backup'=>$safety], aib_update_identity_public($identity)));
    } catch (Throwable $e) {
        aib_json_response(['ok'=>false,'error'=>$e->getMessage()],500);
    }
}

$method = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));
$action = strtolower(trim((string)($_GET['action'] ?? $_POST['action'] ?? $_SERVER['HTTP_X_AIB_ACTION'] ?? 'status')));
if ($method === 'GET' || $action === 'status') aib_json_response(aib_update_status());
if ($action === 'install' && in_array($method,['PUT','POST'],true)) aib_update_install_raw();
if ($action === 'rollback' && $method === 'POST') {
    $in = aib_input();
    aib_update_rollback((string)($in['backup_id'] ?? $in['id'] ?? ''));
}
aib_json_response(['ok'=>false,'error'=>'unsupported_update_action'],400);
