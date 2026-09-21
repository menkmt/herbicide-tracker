<?php
/**
 * [ground_truth_tracker] — the application grid inside any page or post.
 *
 * The full pages are the primary interface; this is for dropping a filtered
 * view into an existing page, such as a county campaign page.
 */

declare(strict_types=1);

if (!defined('ABSPATH')) {
    exit;
}

final class GT_Shortcode
{
    public static function init(): void
    {
        add_shortcode('ground_truth_tracker', [self::class, 'render']);
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
            'ground_truth_tracker'
        );

        $data = GT_Client::get('/api/applications', [
            'county'    => sanitize_title($atts['county']),
            'chemical'  => sanitize_text_field($atts['chemical']),
            'method'    => sanitize_text_field($atts['method']),
            'flagged'   => $atts['flagged'] !== '' ? 'true' : '',
            'page_size' => max(1, min(100, (int) $atts['limit'])),
        ]);

        if (is_wp_error($data)) {
            return '<p class="gt-muted">'
                . esc_html__('The herbicide tracker is temporarily unavailable.', 'ground-truth-tracker')
                . '</p>';
        }

        $renderer = new GT_Renderer();
        $html = '<div class="gt-wrap">' . $renderer->grid($data['applications'] ?? []);
        $html .= sprintf(
            '<p class="gt-small"><a href="%s">%s</a></p>',
            esc_url(GT_Router::url('index')),
            esc_html__('View the full herbicide tracker →', 'ground-truth-tracker')
        );
        return $html . '</div>';
    }
}
