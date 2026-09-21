<?php
/**
 * Plugin Name:       Ground Truth
 * Plugin URI:        https://example.org/herbicide-tracker/
 * Description:       Publishes the the herbicide tracker inside WordPress with real,
 *                    crawlable URLs. Talks to the tracker API; does not embed an iframe.
 * Version:           0.1.0
 * Requires at least: 6.4
 * Requires PHP:      8.1
 * Author:            Ground Truth
 * License:           GPL-2.0-or-later
 * Text Domain:       ground-truth-tracker
 *
 * Design notes
 * ------------
 * The build plan rules out an iframe for the primary interface, and for good
 * reason: an iframe has no URL of its own, so nothing inside it can be linked,
 * shared, indexed or cited. Instead this plugin registers real rewrite rules,
 * renders server-side from the tracker API, and emits its own title, meta
 * description, canonical link and structured data for each page.
 *
 * WordPress stays the presentation layer. No tracker data is copied into the
 * WordPress database; pages are rendered from the API and cached in transients,
 * so there is one source of truth and nothing to keep in sync.
 */

declare(strict_types=1);

if (!defined('ABSPATH')) {
    exit;
}

define('GT_VERSION', '0.1.0');
define('GT_PATH', plugin_dir_path(__FILE__));
define('GT_URL', plugin_dir_url(__FILE__));

require_once GT_PATH . 'includes/class-gt-client.php';
require_once GT_PATH . 'includes/class-gt-router.php';
require_once GT_PATH . 'includes/class-gt-renderer.php';
require_once GT_PATH . 'includes/class-gt-settings.php';
require_once GT_PATH . 'includes/class-gt-shortcode.php';

add_action('plugins_loaded', static function (): void {
    GT_Settings::init();
    GT_Router::init();
    GT_Shortcode::init();
});

/**
 * Rewrite rules only take effect once flushed, so that is done on activation
 * and undone on deactivation rather than on every request.
 */
register_activation_hook(__FILE__, static function (): void {
    GT_Router::register_rules();
    flush_rewrite_rules();
});

register_deactivation_hook(__FILE__, static function (): void {
    flush_rewrite_rules();
});
