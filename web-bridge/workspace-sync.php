<?php
require __DIR__ . '/lib/bootstrap.php';

if (!aib_agent_auth()) {
    aib_json_response(['ok'=>false,'error'=>'invalid_or_missing_token'],401);
}

$in = aib_input();
$action = strtolower(trim((string)($in['action'] ?? $_GET['action'] ?? 'manifest')));

if ($action === 'manifest') {
    aib_json_response(['ok'=>true,'owners'=>aib_workspace_owners(),'server_time'=>aib_now()]);
}

$owner = strtolower(trim((string)($in['owner_hash'] ?? '')));
if (!aib_workspace_owner_valid($owner)) aib_json_response(['ok'=>false,'error'=>'invalid_workspace_owner'],400);

if ($action === 'pull') {
    try {
        aib_json_response([
            'ok'=>true,
            'owner_hash'=>$owner,
            'revision'=>aib_workspace_revision($owner),
            'snapshot'=>aib_workspace_export_snapshot($owner),
            'server_time'=>aib_now(),
        ]);
    } catch (Throwable $e) {
        aib_json_response(['ok'=>false,'error'=>$e->getMessage()],400);
    }
}

if ($action === 'push' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $expected = (int)($in['expected_revision'] ?? 0);
    $current = aib_workspace_revision($owner);
    if ($expected > 0 && $expected !== $current) {
        aib_json_response(['ok'=>false,'error'=>'workspace_revision_conflict','revision'=>$current],409);
    }
    $snapshot = $in['snapshot'] ?? null;
    if (!is_array($snapshot)) aib_json_response(['ok'=>false,'error'=>'snapshot_required'],400);
    try {
        $revision = aib_workspace_import_snapshot($owner,$snapshot);
        aib_json_response(['ok'=>true,'owner_hash'=>$owner,'revision'=>$revision,'server_time'=>aib_now()]);
    } catch (Throwable $e) {
        aib_json_response(['ok'=>false,'error'=>$e->getMessage()],400);
    }
}

aib_json_response(['ok'=>false,'error'=>'unsupported_workspace_sync_action'],400);
