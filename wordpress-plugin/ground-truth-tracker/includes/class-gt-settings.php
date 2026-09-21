<?php
/**
 * Settings screen: where the tracker API lives, and an optional key.
 */

declare(strict_types=1);

if (!defined('ABSPATH')) {
    exit;
}

final class GT_Settings
{
    public static function init(): void
    {
        add_action('admin_menu', [self::class, 'menu']);
        add_action('admin_init', [self::class, 'register']);
        add_action('wp_enqueue_scripts', [self::class, 'styles']);
    }

    public static function menu(): void
    {
        add_options_page(
            __('Herbicide Tracker', 'ground-truth-tracker'),
            __('Herbicide Tracker', 'ground-truth-tracker'),
            'manage_options',
            'ground-truth-tracker',
            [self::class, 'render']
        );
    }

    public static function register(): void
    {
        register_setting('gt', 'gt_api_url', [
            'type'              => 'string',
            'sanitize_callback' => 'esc_url_raw',
            'default'           => 'http://localhost:8000',
        ]);
        register_setting('gt', 'gt_api_key', [
            'type'              => 'string',
            'sanitize_callback' => 'sanitize_text_field',
            'default'           => '',
        ]);
    }

    public static function render(): void
    {
        if (!current_user_can('manage_options')) {
            return;
        }

        if (isset($_POST['gt_flush']) && check_admin_referer('gt_flush')) {
            GT_Client::flush_cache();
            echo '<div class="notice notice-success"><p>'
                . esc_html__('Cached tracker responses cleared.', 'ground-truth-tracker')
                . '</p></div>';
        }

        $status = GT_Client::get('/api/meta', [], 0);
        ?>
        <div class="wrap">
            <h1><?php esc_html_e('Herbicide Tracker', 'ground-truth-tracker'); ?></h1>

            <?php if (is_wp_error($status)) : ?>
                <div class="notice notice-error">
                    <p><?php echo esc_html(sprintf(
                        /* translators: %s: error message */
                        __('Cannot reach the tracker API: %s', 'ground-truth-tracker'),
                        $status->get_error_message()
                    )); ?></p>
                </div>
            <?php else : ?>
                <div class="notice notice-success">
                    <p><?php echo esc_html(sprintf(
                        /* translators: %s: coverage start date */
                        __('Connected. Records covered from %s onward.', 'ground-truth-tracker'),
                        (string) ($status['coverage_start'] ?? '')
                    )); ?></p>
                </div>
            <?php endif; ?>

            <form method="post" action="options.php">
                <?php settings_fields('gt'); ?>
                <table class="form-table" role="presentation">
                    <tr>
                        <th scope="row">
                            <label for="gt_api_url"><?php esc_html_e('Tracker API URL', 'ground-truth-tracker'); ?></label>
                        </th>
                        <td>
                            <input name="gt_api_url" id="gt_api_url" type="url" class="regular-text"
                                   value="<?php echo esc_attr((string) get_option('gt_api_url', '')); ?>" />
                            <p class="description">
                                <?php esc_html_e('The tracker backend. This site calls it server-side; visitors never contact it directly.', 'ground-truth-tracker'); ?>
                            </p>
                        </td>
                    </tr>
                    <tr>
                        <th scope="row">
                            <label for="gt_api_key"><?php esc_html_e('API key', 'ground-truth-tracker'); ?></label>
                        </th>
                        <td>
                            <input name="gt_api_key" id="gt_api_key" type="password" class="regular-text"
                                   autocomplete="off"
                                   value="<?php echo esc_attr((string) get_option('gt_api_key', '')); ?>" />
                            <p class="description">
                                <?php esc_html_e('Optional. Only needed to show subscriber-tier data such as cross-county totals.', 'ground-truth-tracker'); ?>
                            </p>
                        </td>
                    </tr>
                </table>
                <?php submit_button(); ?>
            </form>

            <h2><?php esc_html_e('Cache', 'ground-truth-tracker'); ?></h2>
            <p><?php esc_html_e('Tracker pages are cached for a few minutes. Clear the cache after publishing new applications to show them immediately.', 'ground-truth-tracker'); ?></p>
            <form method="post">
                <?php wp_nonce_field('gt_flush'); ?>
                <button class="button" name="gt_flush" value="1">
                    <?php esc_html_e('Clear cached responses', 'ground-truth-tracker'); ?>
                </button>
            </form>

            <h2><?php esc_html_e('Pages this plugin provides', 'ground-truth-tracker'); ?></h2>
            <p><?php esc_html_e('These are real URLs on this site, not an embedded frame, so each one can be linked, shared and indexed:', 'ground-truth-tracker'); ?></p>
            <ul>
                <li><code>/herbicide-tracker/</code></li>
                <li><code>/herbicide-tracker/{county}/</code></li>
                <li><code>/herbicide-application/{slug}/</code></li>
                <li><code>/chemical/</code> and <code>/chemical/{slug}/</code></li>
                <li><code>/herbicide-tracker-near-me/</code></li>
            </ul>
            <p>
                <?php esc_html_e('If these return 404, re-save Settings → Permalinks to flush rewrite rules.', 'ground-truth-tracker'); ?>
            </p>

            <h2><?php esc_html_e('Shortcode', 'ground-truth-tracker'); ?></h2>
            <p><code>[ground_truth_tracker]</code> —
                <?php esc_html_e('embeds the application grid in any page or post. Attributes: county, chemical, method, limit.', 'ground-truth-tracker'); ?>
            </p>
        </div>
        <?php
    }

    public static function styles(): void
    {
        wp_register_style('gt', GT_URL . 'assets/tracker.css', [], GT_VERSION);
        wp_enqueue_style('gt');
    }
}
