# Build through scripts/build-local-image.py; every source pin comes from build/versions.json.
ARG PHP_BASE_IMAGE
FROM ${PHP_BASE_IMAGE}

ARG SOURCE_DATE_EPOCH=0
ARG OCI_SOURCE=""
ARG OCI_REVISION=""
ARG OCI_VERSION=""
ARG OCI_CREATED=""
LABEL org.opencontainers.image.source="${OCI_SOURCE}" \
	org.opencontainers.image.revision="${OCI_REVISION}" \
	org.opencontainers.image.version="${OCI_VERSION}" \
	org.opencontainers.image.licenses="GPL-2.0-only" \
	org.opencontainers.image.created="${OCI_CREATED}"

ARG IMAGICK_URL
ARG IMAGICK_SHA256
ARG REDIS_URL
ARG REDIS_SHA256
ARG APCU_URL
ARG APCU_SHA256
# Persistent runtime dependencies.
RUN set -eux; \
	apk add --no-cache \
		bash \
		ffmpeg \
		ghostscript \
		imagemagick; \
	find /etc/fonts /usr/share/fonts -exec touch -h -d "@${SOURCE_DATE_EPOCH}" '{}' +; \
	rm -rf /var/cache/fontconfig/*; \
	fc-cache -f; \
	rm -f /var/log/apk.log

# Fail closed on the official pinned-base libiconv runtime before compiling anything.
RUN set -eux; \
	php -r 'if (ICONV_IMPL !== "libiconv") { fwrite(STDERR, "unexpected ICONV_IMPL: " . ICONV_IMPL . "\n"); exit(1); } if (ICONV_VERSION !== "1.18") { fwrite(STDERR, "unexpected ICONV_VERSION: " . ICONV_VERSION . "\n"); exit(1); }'; \
	apk info -e gnu-libiconv-libs=1.18-r0; \
	[ "$(apk info -W /usr/lib/libiconv.so.2)" = '/usr/lib/libiconv.so.2 is owned by gnu-libiconv-libs-1.18-r0' ]; \
	[ "$(readlink -f /usr/lib/libiconv.so.2)" = '/usr/lib/libiconv.so.2.7.0' ]; \
	iconvAudit="$(apk audit --system /usr/lib)" || { auditRc=$?; echo "apk audit failed with status $auditRc" >&2; exit "$auditRc"; }; \
	! printf '%s\n' "$iconvAudit" | grep -E '^[^[:space:]]+[[:space:]]+usr/lib/(libiconv|libcharset)\.so([./]|$)'; \
	phpLdd="$(ldd /usr/local/bin/php)"; printf '%s\n' "$phpLdd"; \
	! printf '%s\n' "$phpLdd" | grep 'not found'; \
	printf '%s\n' "$phpLdd" | grep -F '/usr/lib/libiconv.so.2'; \
	php -r '$result = iconv("UTF-8", "ASCII//TRANSLIT", "café"); if ($result === false || stripos($result, "caf") !== 0 || !preg_match("/^[\\x00-\\x7F]+$/", $result)) { fwrite(STDERR, "iconv transliteration failed\n"); exit(1); }'; \
	php-fpm -t

# Build every non-Alpine source from an approved archive and verify it before extraction.
RUN set -eux; \
	apk add --no-cache --virtual .build-deps \
		$PHPIZE_DEPS \
		coreutils \
		curl \
		freetype-dev \
		icu-dev \
		imagemagick-dev \
		libjpeg-turbo-dev \
		libpng-dev \
		libwebp-dev \
		libzip-dev \
	; \
	mkdir -p /usr/src/vendor; \
	docker-php-source extract; \
	cd /usr/src/php; \
	docker-php-ext-configure gd --with-freetype --with-jpeg --with-webp; \
	docker-php-ext-configure intl; \
	docker-php-ext-install -j "$(nproc)" bcmath exif gd intl mysqli zip pdo pdo_mysql; \
	for extension in imagick redis apcu; do mkdir -p "/usr/src/php/ext/$extension"; done; \
	curl -fsSL "$IMAGICK_URL" -o /usr/src/vendor/imagick.tgz; \
	echo "$IMAGICK_SHA256  /usr/src/vendor/imagick.tgz" | sha256sum -c -; \
	tar -xzf /usr/src/vendor/imagick.tgz -C /usr/src/php/ext/imagick --strip-components=1; \
	curl -fsSL "$REDIS_URL" -o /usr/src/vendor/redis.tgz; \
	echo "$REDIS_SHA256  /usr/src/vendor/redis.tgz" | sha256sum -c -; \
	tar -xzf /usr/src/vendor/redis.tgz -C /usr/src/php/ext/redis --strip-components=1; \
	curl -fsSL "$APCU_URL" -o /usr/src/vendor/apcu.tgz; \
	echo "$APCU_SHA256  /usr/src/vendor/apcu.tgz" | sha256sum -c -; \
	tar -xzf /usr/src/vendor/apcu.tgz -C /usr/src/php/ext/apcu --strip-components=1; \
	docker-php-ext-install -j "$(nproc)" imagick redis apcu; \
	docker-php-source delete; \
	rm -rf /usr/src/vendor /tmp/pear; \
	out="$(php -r 'exit(0);')"; [ -z "$out" ]; \
	err="$(php -r 'exit(0);' 3>&1 1>&2 2>&3)"; [ -z "$err" ]; \
	extDir="$(php -r 'echo ini_get("extension_dir");')"; [ -d "$extDir" ]; \
	runDeps="$(scanelf --needed --nobanner --format '%n#p' --recursive "$extDir" \
		| tr ',' '\n' \
		| sort -u \
		| awk 'system("[ -e /usr/local/lib/" $1 " ]") == 0 { next } { print "so:" $1 }')"; \
	apk add --no-network --virtual .wordpress-phpexts-rundeps=1 $runDeps; \
	rundepsChecksum="$(printf '%s\n' '.wordpress-phpexts-rundeps=1' "$runDeps" | php -r 'echo base64_encode(sha1(stream_get_contents(STDIN), true));')"; \
	awk -v checksum="Q1$rundepsChecksum" 'BEGIN { RS=""; ORS="\n\n" } { if ($0 ~ "(^|\n)P:[.]wordpress-phpexts-rundeps(\n|$)") sub(/^C:[^\n]*/, "C:" checksum); print }' /lib/apk/db/installed > /lib/apk/db/installed.tmp; \
	mv /lib/apk/db/installed.tmp /lib/apk/db/installed; \
	apk info -e .wordpress-phpexts-rundeps=1; \
	grep -F "C:Q1$rundepsChecksum" /lib/apk/db/installed; \
	apk del --no-network .build-deps; \
	! { ldd "$extDir"/*.so | grep 'not found'; }; \
	php -v; \
	php -m >/dev/null; \
	php-fpm -t; \
	phpLdd="$(ldd /usr/local/bin/php)"; printf '%s\n' "$phpLdd"; \
	! printf '%s\n' "$phpLdd" | grep 'not found'; \
	printf '%s\n' "$phpLdd" | grep -F '/usr/lib/libiconv.so.2'; \
	rm -f /var/log/apk.log

# Recommended PHP.ini settings.
RUN set -eux; \
	{ \
		echo 'opcache.memory_consumption=128'; \
		echo 'opcache.interned_strings_buffer=8'; \
		echo 'opcache.max_accelerated_files=4000'; \
		echo 'opcache.revalidate_freq=2'; \
		echo 'opcache.enable=1'; \
		echo 'opcache.jit_buffer_size=100M'; \
		echo 'opcache.jit=tracing'; \
	} > /usr/local/etc/php/conf.d/opcache-recommended.ini

RUN { \
		echo 'error_reporting = E_ERROR | E_WARNING | E_PARSE | E_CORE_ERROR | E_CORE_WARNING | E_COMPILE_ERROR | E_COMPILE_WARNING | E_RECOVERABLE_ERROR'; \
		echo 'display_errors = Off'; \
		echo 'display_startup_errors = Off'; \
		echo 'log_errors = On'; \
		echo 'error_log = /dev/stderr'; \
		echo 'ignore_repeated_errors = On'; \
		echo 'ignore_repeated_source = Off'; \
		echo 'html_errors = Off'; \
	} > /usr/local/etc/php/conf.d/error-logging.ini
