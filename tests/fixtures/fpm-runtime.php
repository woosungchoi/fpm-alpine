<?php
// Executed by the actual FPM worker; only selected runtime values are returned.
header('Content-Type: application/json');
function require_ok($ok, $message) { if (!$ok) { throw new RuntimeException($message); } }
$root = sys_get_temp_dir() . '/fpm-smoke-' . bin2hex(random_bytes(8));
mkdir($root, 0700);
try {
    require_ok(PHP_SAPI === 'fpm-fcgi', 'FPM SAPI required');
    $extensions = [];
    foreach (['bcmath','exif','gd','intl','mysqli','zip','pdo','pdo_mysql','imagick','redis','apcu','Zend OPcache'] as $name) {
        require_ok(extension_loaded($name), "missing extension: $name");
        $extensions[$name] = phpversion($name);
    }
    $ini = [];
    foreach (['opcache.enable','opcache.memory_consumption','opcache.interned_strings_buffer',
              'opcache.max_accelerated_files','opcache.revalidate_freq','opcache.jit','opcache.jit_buffer_size',
              'display_errors','display_startup_errors','log_errors','error_log','html_errors'] as $key) {
        $ini[$key] = ini_get($key);
    }
    $status = opcache_get_status(false);
    require_ok(is_array($status) && $status['opcache_enabled'], 'OPcache inactive in FPM');
    $images = [];
    foreach (['jpeg','png','webp'] as $format) {
        // Generate tiny pixels, then exercise each codec's decoder, resize and encoder.
        $source = "$root/source.$format";
        $im = imagecreatetruecolor(32, 24);
        imagefill($im, 0, 0, imagecolorallocate($im, 220, 80, 40));
        $encode = 'image' . $format;
        require_ok($encode($im, $source), "$format GD encode");
        imagedestroy($im);
        $decode = 'imagecreatefrom' . $format;
        $im = $decode($source);
        require_ok($im !== false && imagesx($im) === 32, "$format GD decode");
        $small = imagescale($im, 16, 12);
        require_ok($encode($small, "$root/gd.$format"), "$format GD resize encode");
        imagedestroy($small); imagedestroy($im);
        $im = new Imagick($source);
        require_ok($im->resizeImage(16, 12, Imagick::FILTER_LANCZOS, 1), "$format Imagick resize");
        $im->setImageFormat($format);
        $im->writeImage("$root/imagick.$format");
        $im->clear();
        $result = new Imagick("$root/imagick.$format");
        require_ok($result->getImageWidth() === 16 && $result->getImageHeight() === 12, "$format output dimensions");
        $gdResult = $decode("$root/gd.$format");
        require_ok($gdResult !== false && imagesx($gdResult) === 16 && imagesy($gdResult) === 12, "$format GD output dimensions");
        imagedestroy($gdResult);
        $images[$format] = [16, 12]; $result->clear();
    }
    $gif = new Imagick();
    foreach (['red', 'blue'] as $color) {
        $frame = new Imagick(); $frame->newImage(32, 24, $color, 'gif');
        $frame->setImageDelay(10); $gif->addImage($frame); $frame->clear();
    }
    $gif->writeImages("$root/input.gif", true); $gif->clear();
    $media = [];
    foreach (['mp4'=>'libx264', 'webm'=>'libvpx-vp9'] as $format=>$encoder) {
        $output = "$root/output.$format";
        exec('ffmpeg -nostdin -v error -y -i ' . escapeshellarg("$root/input.gif") .
             ' -threads 1 -frames:v 2 -c:v ' . $encoder . ' -threads 1 -pix_fmt yuv420p ' . escapeshellarg($output) . ' 2>&1', $log, $rc);
        require_ok($rc === 0, "$format conversion failed: " . implode("\n", $log));
        exec('ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=codec_name,width,height,nb_read_frames -of json ' . escapeshellarg($output), $probe, $rc);
        $data = json_decode(implode("\n", $probe), true); $probe = [];
        $stream = $data['streams'][0] ?? [];
        require_ok($rc === 0 && ($stream['codec_name'] ?? '') === ($format === 'mp4' ? 'h264' : 'vp9') &&
                   ($stream['width'] ?? 0) === 32 && ($stream['height'] ?? 0) === 24 &&
                   (int)($stream['nb_read_frames'] ?? 0) === 2, "$format output contract");
        $media[$format] = $stream;
    }
    file_put_contents("$root/malformed", 'invalid-media');
    exec('ffmpeg -nostdin -v error -i ' . escapeshellarg("$root/malformed") . ' -f null - 2>/dev/null', $log, $rc);
    require_ok($rc !== 0, 'malformed media accepted');
    try { $invalid = new Imagick("$root/malformed"); throw new RuntimeException('malformed image accepted'); }
    catch (ImagickException $expected) {}
    echo json_encode(['ok'=>true, 'sapi'=>PHP_SAPI, 'phpVersion'=>PHP_VERSION,
                      'extensions'=>$extensions, 'ini'=>$ini, 'opcacheEnabled'=>$status['opcache_enabled'],
                      'jit'=>$status['jit'] ?? [], 'images'=>$images, 'media'=>$media]);
} catch (Throwable $e) {
    http_response_code(500); echo json_encode(['ok'=>false, 'error'=>$e->getMessage()]);
} finally {
    foreach (glob("$root/*") as $file) { unlink($file); } rmdir($root);
}
