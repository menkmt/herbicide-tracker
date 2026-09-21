<?php
/**
 * HTTP client for the tracker API, with caching.
 *
 * Responses are cached in transients. Published records only change when an
 * administrator publishes something, so a few minutes of caching removes
 * almost all API traffic without making the site meaningfully stale.
 */

declare(strict_types=1);

if (!defined('ABSPATH')) {
    exit;
}

final class GT_Client
{
    private const CACHE_PREFIX = 'gt_';

    public static function base_url(): string
    {
        return untrailingslashit((string) get_option('gt_api_url', 'http://localhost:8000'));
    }

    /**
     * GET a tracker API path and decode it.
     *
     * @param string               $path  API path beginning with a slash.
     * @param array<string, mixed> $query Query parameters.
     * @return array<string, mixed>|WP_Error
     */
    public static function get(string $path, array $query = [], int $ttl = 300)
    {
        $url = self::base_url() . $path;
        if ($query !== []) {
            $url = add_query_arg(array_filter($query, static fn($v) => $v !== null && $v !== ''), $url);
        }

        $cache_key = self::CACHE_PREFIX . md5($url);
        if ($ttl > 0) {
            $cached = get_transient($cache_key);
            if ($cached !== false) {
                return $cached;
            }
        }

        $headers = ['Accept' => 'application/json'];
        $api_key = (string) get_option('gt_api_key', '');
        if ($api_key !== '') {
            $headers['X-API-Key'] = $api_key;
        }

        $response = wp_remote_get($url, [
            'timeout' => 15,
            'headers' => $headers,
        ]);

        if (is_wp_error($response)) {
            return $response;
        }

        $code = wp_remote_retrieve_response_code($response);
        if ($code !== 200) {
            return new WP_Error(
                'gt_api_error',
                sprintf(
                    /* translators: 1: API path, 2: HTTP status code */
                    __('The herbicide tracker API returned %2$d for %1$s.', 'ground-truth-tracker'),
                    $path,
                    $code
                ),
                ['status' => $code]
            );
        }

        $decoded = json_decode(wp_remote_retrieve_body($response), true);
        if (!is_array($decoded)) {
            return new WP_Error('gt_bad_json', __('The tracker API returned an unreadable response.', 'ground-truth-tracker'));
        }

        if ($ttl > 0) {
            set_transient($cache_key, $decoded, $ttl);
        }
        return $decoded;
    }

    /** Clears every cached tracker response. Used after a publish. */
    public static function flush_cache(): void
    {
        global $wpdb;
        $like = $wpdb->esc_like('_transient_' . self::CACHE_PREFIX) . '%';
        $wpdb->query($wpdb->prepare("DELETE FROM {$wpdb->options} WHERE option_name LIKE %s", $like));
    }
}
