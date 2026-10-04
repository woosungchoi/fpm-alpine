<?php
// Local TCP FastCGI client: no host ports, downloads or additional runtime packages.
function record($type, $data) { return pack('CCnnCC', 1, $type, 1, strlen($data), 0, 0) . $data; }
function length($value) { $n = strlen($value); return $n < 128 ? chr($n) : pack('N', $n | 0x80000000); }
function exact($socket, $n) {
    $out = ''; while (strlen($out) < $n) {
        $part = fread($socket, $n - strlen($out));
        if ($part === false || $part === '') { throw new RuntimeException('truncated FastCGI response'); }
        $out .= $part;
    } return $out;
}
try {
    $socket = stream_socket_client('tcp://127.0.0.1:9000', $errno, $error, 10);
    if (!$socket) { throw new RuntimeException("FPM listener: $error"); }
    stream_set_timeout($socket, 60);
    $params = ['SCRIPT_FILENAME'=>'/tmp/fpm-runtime.php', 'REQUEST_METHOD'=>'GET',
               'SCRIPT_NAME'=>'/fpm-runtime.php', 'SERVER_PROTOCOL'=>'HTTP/1.1',
               'CONTENT_LENGTH'=>'0', 'REMOTE_ADDR'=>'127.0.0.1', 'SERVER_NAME'=>'localhost', 'SERVER_PORT'=>'9000'];
    $body = ''; foreach ($params as $key=>$value) { $body .= length($key) . length($value) . $key . $value; }
    $request = record(1, pack('nCxxxxx', 1, 0)) . record(4, $body) . record(4, '') . record(5, '');
    $offset = 0; while ($offset < strlen($request)) {
        $n = fwrite($socket, substr($request, $offset));
        if (!$n) { throw new RuntimeException('FastCGI write failed'); } $offset += $n;
    }
    $stdout = ''; $stderr = '';
    while (true) {
        $header = unpack('Cversion/Ctype/nid/nsize/Cpadding/Creserved', exact($socket, 8));
        if ($header['version'] !== 1 || $header['id'] !== 1) { throw new RuntimeException('FastCGI header mismatch'); }
        $data = $header['size'] ? exact($socket, $header['size']) : '';
        if ($header['padding']) { exact($socket, $header['padding']); }
        if ($header['type'] === 6) { $stdout .= $data; }
        if ($header['type'] === 7) { $stderr .= $data; }
        if ($header['type'] === 3) {
            $end = unpack('Napp/Cprotocol', $data);
            if ($end['app'] || $end['protocol']) { throw new RuntimeException('FastCGI end status failed'); } break;
        }
    }
    fclose($socket);
    [$headers, $json] = explode("\r\n\r\n", $stdout, 2);
    $result = json_decode($json, true, 512, JSON_THROW_ON_ERROR);
    if (preg_match('/^Status: [45]/m', $headers) || ($result['ok'] ?? false) !== true) {
        throw new RuntimeException('FPM request failed: ' . ($result['error'] ?? 'invalid response'));
    }
    if ($stderr !== '') { throw new RuntimeException('FastCGI stderr: ' . $stderr); }
    $expected = ['opcache.enable'=>'1', 'opcache.memory_consumption'=>'128', 'opcache.interned_strings_buffer'=>'8',
                 'opcache.max_accelerated_files'=>'4000', 'opcache.revalidate_freq'=>'2', 'opcache.jit'=>'tracing',
                 'opcache.jit_buffer_size'=>'100M', 'display_errors'=>'', 'display_startup_errors'=>'',
                 'log_errors'=>'1', 'error_log'=>'/dev/stderr', 'html_errors'=>''];
    foreach ($expected as $key=>$value) {
        if (($result['ini'][$key] ?? null) !== $value) { throw new RuntimeException("FPM ini mismatch: $key"); }
    }
    if (($result['sapi'] ?? '') !== 'fpm-fcgi' || $result['phpVersion'] !== $argv[1]) { throw new RuntimeException('FPM PHP patch/SAPI mismatch'); }
    foreach (['imagick'=>2, 'redis'=>3, 'apcu'=>4] as $name=>$index) {
        if ($result['extensions'][$name] !== $argv[$index]) { throw new RuntimeException("FPM extension version: $name"); }
    }
    echo json_encode($result, JSON_UNESCAPED_SLASHES) . "\n";
} catch (Throwable $e) { fwrite(STDERR, $e->getMessage() . "\n"); exit(1); }
