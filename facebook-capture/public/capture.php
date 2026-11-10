<?php

declare(strict_types=1);

require_once __DIR__ . '/../src/bootstrap.php';

header('Content-Type: application/json');

$provided = Auth::extractToken($_SERVER, $_GET, $_POST);
if (!Auth::isValid($provided, FB_CAPTURE_TOKEN)) {
    http_response_code(401);
    echo json_encode(['error' => 'unauthorized']);
    exit;
}

$store = new Store(FB_CAPTURE_DATA_DIR);
$controller = new CaptureController($store);
[$status, $body] = $controller->handle(file_get_contents('php://input'));

http_response_code($status);
echo $body;
