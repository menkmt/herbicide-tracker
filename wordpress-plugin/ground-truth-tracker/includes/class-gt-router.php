<?php
/**
 * Real, crawlable URLs for tracker pages.
 *
 * Each of these is a genuine WordPress request with its own URL, title and
 * canonical link, so individual applications and chemicals can be linked,
 * shared, indexed and cited — which an iframe cannot do.
 */

declare(strict_types=1);

if (!defined('ABSPATH')) {
    exit;
}

final class GT_Router
{
    /** @var array<string, string> route name => rewrite regex */
    private const ROUTES = [
        'index'       => '^herbicide-tracker/?$',
        'county'      => '^herbicide-tracker/([^/]+)/?$',
        'application' => '^herbicide-application/([^/]+)/?$',
        'chemicals'   => '^chemical/?$',
        'chemical'    => '^chemical/([^/]+)/?$',
        'near'        => '^herbicide-tracker-near-me/?$',
    ];

    public static function init(): void
    {
        add_action('init', [self::class, 'register_rules']);
        add_filter('query_vars', [self::class, 'query_vars']);
        add_action('template_redirect', [self::class, 'dispatch']);
    }

    public static function register_rules(): void
    {
        add_rewrite_rule(self::ROUTES['index'], 'index.php?gt_route=index', 'top');
        add_rewrite_rule(self::ROUTES['county'], 'index.php?gt_route=county&gt_slug=$matches[1]', 'top');
        add_rewrite_rule(self::ROUTES['application'], 'index.php?gt_route=application&gt_slug=$matches[1]', 'top');
        add_rewrite_rule(self::ROUTES['chemicals'], 'index.php?gt_route=chemicals', 'top');
        add_rewrite_rule(self::ROUTES['chemical'], 'index.php?gt_route=chemical&gt_slug=$matches[1]', 'top');
        add_rewrite_rule(self::ROUTES['near'], 'index.php?gt_route=near', 'top');
    }

    /**
     * @param string[] $vars
     * @return string[]
     */
    public static function query_vars(array $vars): array
    {
        $vars[] = 'gt_route';
        $vars[] = 'gt_slug';
        return $vars;
    }

    public static function dispatch(): void
    {
        $route = get_query_var('gt_route');
        if ($route === '' || $route === false) {
            return;
        }
        $slug = sanitize_title((string) get_query_var('gt_slug'));

        $renderer = new GT_Renderer();
        $page = $renderer->build((string) $route, $slug);

        if ($page === null) {
            // A missing application must be a real 404, not a page saying
            // "not found" with a 200 status: search engines and archivers
            // treat those very differently.
            status_header(404);
            nocache_headers();
            global $wp_query;
            $wp_query->set_404();
            get_template_part('404');
            exit;
        }

        add_filter('pre_get_document_title', static fn() => $page['title'], 20);
        add_action('wp_head', static function () use ($page): void {
            printf(
                '<meta name="description" content="%s" />' . "\n",
                esc_attr($page['description'])
            );
            printf('<link rel="canonical" href="%s" />' . "\n", esc_url($page['canonical']));
            printf('<meta property="og:title" content="%s" />' . "\n", esc_attr($page['title']));
            printf('<meta property="og:description" content="%s" />' . "\n", esc_attr($page['description']));
            printf('<meta property="og:url" content="%s" />' . "\n", esc_url($page['canonical']));
            printf('<meta property="og:type" content="%s" />' . "\n", esc_attr($page['og_type']));
            if (!empty($page['schema'])) {
                printf(
                    '<script type="application/ld+json">%s</script>' . "\n",
                    wp_json_encode($page['schema'], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)
                );
            }
        });

        // Render inside the active theme so the tracker inherits Protect
        // Lassen's own design rather than looking like a bolted-on app.
        get_header();
        echo '<div class="gt-wrap">' . $page['body'] . '</div>';
        get_footer();
        exit;
    }

    public static function url(string $route, string $slug = ''): string
    {
        return match ($route) {
            'county'      => home_url("/herbicide-tracker/{$slug}/"),
            'application' => home_url("/herbicide-application/{$slug}/"),
            'chemical'    => home_url("/chemical/{$slug}/"),
            'chemicals'   => home_url('/chemical/'),
            'near'        => home_url('/herbicide-tracker-near-me/'),
            default       => home_url('/herbicide-tracker/'),
        };
    }
}
