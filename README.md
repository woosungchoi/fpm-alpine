# fpm-alpine

## Based on wordpress:php7.4-fpm-alpine Dockerfile

`Source` : [https://github.com/docker-library/wordpress/tree/master/latest](https://github.com/docker-library/wordpress/tree/master/latest)

## Added ffmpeg and redis extensions

You can convert animated `gif` images to `mp4` or `webm` with `ffmpeg`.

By adding the `redis` extension, you can communicate with the `redis server` to perform the cache function.

## Added extensions for Rhymix 2.0

pdo, pdo_mysql, apcu, intl

## Fix iconv function

The iconv function on the php alpine image has been modified to work well.

## Added PHP 8.0 branch

https://github.com/woosungchoi/fpm-alpine/tree/8.0

## Repositories where this image is being used

### docker-wordpress

`Source` : https://github.com/woosungchoi/docker-wordpress

Clean Wordpress CMS + Docker (development & production)

### docker-gnuboard

`Source` : https://github.com/woosungchoi/docker-gnuboard

Clean Gnuboard CMS + Docker (development & production)

### docker-rhymix

` Source` : https://github.com/woosungchoi/docker-rhymix

Clean Rhymix CMS + Docker (development & production)

### docker-multi-site

`Source` : https://github.com/woosungchoi/docker-multi-site

Docker with wordpress, gnuboard, rhymix
