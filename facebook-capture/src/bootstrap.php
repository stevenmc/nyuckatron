<?php

declare(strict_types=1);

/**
 * Shared setup for both public endpoints: load the classes (plain
 * require_once, no Composer autoloader -- this is six small files, not
 * worth the dependency) and the token from ../.env, which sits outside
 * the webroot same as this file does.
 */

require_once __DIR__ . '/Env.php';
require_once __DIR__ . '/Auth.php';
require_once __DIR__ . '/Store.php';
require_once __DIR__ . '/CaptureController.php';
require_once __DIR__ . '/FeedController.php';

$env = Env::load(__DIR__ . '/../.env');

define('FB_CAPTURE_TOKEN', $env['FB_CAPTURE_TOKEN'] ?? '');
define('FB_CAPTURE_DATA_DIR', __DIR__ . '/../data');
