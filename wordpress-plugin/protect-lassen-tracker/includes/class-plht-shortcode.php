<?php
/**
 * [protect_lassen_tracker] — the application grid inside any page or post.
 *
 * The full pages are the primary interface; this is for dropping a filtered
 * view into an existing page, such as a county campaign page.
 */

declare(strict_types=1);

if (!defined('ABSPATH')) {
    exit;
}

final class PLHT_Shortcode
{
    public static function init(): void
    {
        add_shortcode('protect_lassen_tracker', [self::class, 'render']);
    }

    /**
     * @param array<string, string>|string $atts
     */
    public static function render($atts = []): string
    {
        $atts = shortcode_atts(
            [
                'county'   => '',
                'chemical' => '',
                'method'   => '',
                'flagged'  => '',
                'limit'    => '25',
            ],
            $atts,
            'protect_lassen_tracker'
        );

        $data = PLHT_Client::get('/api/applications', [
            'county'    => sanitize_title($atts['county']),
            'chemical'  => sanitize_text_field($atts['chemical']),
            'method'    => sanitize_text_field($atts['method']),
            'flagged'   => $atts['flagged'] !== '' ? 'true' : '',
            'page_size' => max(1, min(100, (int) $atts['limit'])),
        ]);

        if (is_wp_error($data)) {
            return '<p class="plht-muted">'
                . esc_html__('The herbicide tracker is temporarily unavailable.', 'protect-lassen-tracker')
                . '</p>';
        }

        $renderer = new PLHT_Renderer();
        $html = '<div class="plht-wrap">' . $renderer->grid($data['applications'] ?? []);
        $html .= sprintf(
            '<p class="plht-small"><a href="%s">%s</a></p>',
            esc_url(PLHT_Router::url('index')),
            esc_html__('View the full herbicide tracker →', 'protect-lassen-tracker')
        );
        return $html . '</div>';
    }
}
