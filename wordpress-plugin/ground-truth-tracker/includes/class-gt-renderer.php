<?php
/**
 * Renders tracker pages as HTML inside the active theme.
 *
 * Everything from the API is treated as untrusted and escaped on output. The
 * markup mirrors the standalone front end so the two stay recognisably the
 * same product.
 */

declare(strict_types=1);

if (!defined('ABSPATH')) {
    exit;
}

final class GT_Renderer
{
    /**
     * Build a page.
     *
     * @return array{title:string,description:string,canonical:string,og_type:string,schema:array<string,mixed>,body:string}|null
     *         Null when the record does not exist, which becomes a real 404.
     */
    public function build(string $route, string $slug): ?array
    {
        return match ($route) {
            'index'       => $this->index_page(),
            'county'      => $this->county_page($slug),
            'application' => $this->application_page($slug),
            'chemicals'   => $this->chemicals_page(),
            'chemical'    => $this->chemical_page($slug),
            'near'        => $this->near_page(),
            default       => null,
        };
    }

    private function index_page(): ?array
    {
        $counties = GT_Client::get('/api/counties');
        $recent = GT_Client::get('/api/applications', ['page_size' => 25]);
        if (is_wp_error($counties) || is_wp_error($recent)) {
            return $this->error_page($counties instanceof WP_Error ? $counties : $recent);
        }

        $body = '<h1>' . esc_html__('Herbicide tracker', 'ground-truth-tracker') . '</h1>';
        $body .= '<p class="gt-lede">' . esc_html__(
            'Forestry herbicide and pesticide applications reported to California county agricultural commissioners.',
            'ground-truth-tracker'
        ) . '</p>';

        $body .= '<h2>' . esc_html__('Browse by county', 'ground-truth-tracker') . '</h2><ul class="gt-counties">';
        foreach ($counties['counties'] ?? [] as $county) {
            $body .= sprintf(
                '<li><a href="%s">%s</a> <span class="gt-muted">%s</span></li>',
                esc_url(GT_Router::url('county', (string) $county['slug'])),
                esc_html((string) $county['name']),
                esc_html(sprintf(
                    /* translators: 1: number of applications, 2: acreage */
                    __('%1$d applications · %2$s acres', 'ground-truth-tracker'),
                    (int) $county['applications'],
                    number_format_i18n((float) $county['acres'])
                ))
            );
        }
        $body .= '</ul>';

        $body .= '<h2>' . esc_html__('Recent applications', 'ground-truth-tracker') . '</h2>';
        $body .= $this->grid($recent['applications'] ?? []);

        return [
            'title'       => __('Herbicide Tracker', 'ground-truth-tracker'),
            'description' => __('Forestry herbicide applications reported to California county agricultural commissioners.', 'ground-truth-tracker'),
            'canonical'   => GT_Router::url('index'),
            'og_type'     => 'website',
            'schema'      => [],
            'body'        => $body,
        ];
    }

    private function county_page(string $slug): ?array
    {
        $counties = GT_Client::get('/api/counties');
        if (is_wp_error($counties)) {
            return $this->error_page($counties);
        }
        $match = null;
        foreach ($counties['counties'] ?? [] as $county) {
            if ((string) $county['slug'] === $slug) {
                $match = $county;
                break;
            }
        }
        if ($match === null) {
            return null;
        }

        $data = GT_Client::get('/api/applications', ['county' => $slug, 'page_size' => 50]);
        if (is_wp_error($data)) {
            return $this->error_page($data);
        }

        $body = sprintf('<h1>%s</h1>', esc_html(sprintf(
            /* translators: %s: county name */
            __('%s County herbicide applications', 'ground-truth-tracker'),
            (string) $match['name']
        )));
        $body .= '<p class="gt-lede">' . esc_html(sprintf(
            /* translators: 1: applications, 2: acres */
            __('%1$d published applications covering %2$s reported acres.', 'ground-truth-tracker'),
            (int) $match['applications'],
            number_format_i18n((float) $match['acres'])
        )) . '</p>';
        $body .= $this->grid($data['applications'] ?? []);

        return [
            'title'       => sprintf(__('%s County', 'ground-truth-tracker'), (string) $match['name']),
            'description' => sprintf(
                __('Forestry herbicide applications reported in %s County, California.', 'ground-truth-tracker'),
                (string) $match['name']
            ),
            'canonical'   => GT_Router::url('county', $slug),
            'og_type'     => 'website',
            'schema'      => [],
            'body'        => $body,
        ];
    }

    private function application_page(string $slug): ?array
    {
        $application = GT_Client::get('/api/applications/' . rawurlencode($slug));
        if (is_wp_error($application)) {
            $status = $application->get_error_data()['status'] ?? 0;
            return $status === 404 ? null : $this->error_page($application);
        }

        $when = $this->date_range($application['date_start'] ?? null, $application['date_end'] ?? null);
        $acres = $application['acres'] !== null
            ? number_format_i18n((float) $application['acres'], 1)
            : __('not reported', 'ground-truth-tracker');

        $body = sprintf('<h1>%s</h1>', esc_html((string) $application['title']));
        $body .= sprintf(
            '<p class="gt-lede">%s</p>',
            esc_html(sprintf(
                /* translators: 1: date range, 2: acres, 3: method */
                __('%1$s · %2$s acres reported treated · %3$s application', 'ground-truth-tracker'),
                $when,
                $acres,
                $application['method'] === 'aerial'
                    ? __('Aerial', 'ground-truth-tracker')
                    : __('Ground', 'ground-truth-tracker')
            ))
        );

        if (!empty($application['is_planned'])) {
            $body .= '<p class="gt-notice gt-warn">' . esc_html__(
                'This is a notice of intent. It records that the operator told the county they intended to apply a restricted material. It is not a report that the application took place.',
                'ground-truth-tracker'
            ) . '</p>';
        }

        $body .= '<h2>' . esc_html__('Chemical warnings', 'ground-truth-tracker') . '</h2>';
        $flags = $application['flags']['flags'] ?? [];
        if ($flags === []) {
            $body .= '<p class="gt-muted">' . esc_html__('No restricted-material or watchlist flags are recorded.', 'ground-truth-tracker') . '</p>';
        } else {
            foreach ($flags as $flag) {
                $body .= sprintf(
                    '<div class="gt-flag gt-flag-%s"><strong>%s</strong><div>%s</div><div class="gt-src">%s %s</div></div>',
                    esc_attr((string) $flag['level']),
                    esc_html((string) $flag['label']),
                    esc_html((string) $flag['detail']),
                    esc_html(
                        !empty($flag['is_regulatory'])
                            ? __('Regulatory status, from:', 'ground-truth-tracker')
                            : __('Ground Truth editorial flag, from:', 'ground-truth-tracker')
                    ),
                    esc_html((string) $flag['source_citation'])
                );
            }
        }

        $body .= '<h2>' . esc_html__('Where', 'ground-truth-tracker') . '</h2>';
        $body .= '<dl class="gt-facts">';
        $body .= $this->fact(__('Township / range / section', 'ground-truth-tracker'), implode(', ', $application['mtrs'] ?? []));
        $body .= $this->fact(__('PUR site IDs', 'ground-truth-tracker'), implode(', ', $application['site_ids'] ?? []));
        $body .= $this->fact(__('Permit numbers', 'ground-truth-tracker'), implode(', ', $application['permit_numbers'] ?? []));
        $body .= $this->fact(__('Property owner / operator', 'ground-truth-tracker'), (string) ($application['owner'] ?? ''));
        $body .= '</dl>';

        if (!empty($application['parcels'])) {
            $body .= '<p class="gt-muted gt-small">' . esc_html__(
                'The parcels below are recorded to the operator named on this application and lie within the sections it reports. They show property boundaries, not the area actually sprayed.',
                'ground-truth-tracker'
            ) . '</p><ul>';
            foreach ($application['parcels'] as $parcel) {
                $body .= sprintf(
                    '<li>%s — %s</li>',
                    esc_html((string) $parcel['apn']),
                    esc_html((string) ($parcel['owner'] ?? ''))
                );
            }
            $body .= '</ul>';
        }

        $body .= '<h2>' . esc_html__('Pesticide use reports', 'ground-truth-tracker') . '</h2>';
        $body .= '<table class="gt-records"><thead><tr>';
        foreach ([
            __('Report', 'ground-truth-tracker'),
            __('Location', 'ground-truth-tracker'),
            __('Date', 'ground-truth-tracker'),
            __('Applicator', 'ground-truth-tracker'),
            __('Products', 'ground-truth-tracker'),
            __('Acres', 'ground-truth-tracker'),
        ] as $heading) {
            $body .= '<th>' . esc_html($heading) . '</th>';
        }
        $body .= '</tr></thead><tbody>';
        foreach ($application['records'] ?? [] as $record) {
            $products = [];
            foreach ($record['products'] ?? [] as $product) {
                $products[] = trim(sprintf(
                    '%s %s %s',
                    (string) ($product['name'] ?? ''),
                    $product['quantity'] !== null ? '— ' . $product['quantity'] : '',
                    (string) ($product['units'] ?? '')
                ));
            }
            $body .= sprintf(
                '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>',
                esc_html((string) ($record['document_number'] ?? '')),
                esc_html((string) ($record['mtrs'] ?? '')),
                esc_html((string) ($record['date_start'] ?? '')),
                esc_html((string) ($record['applicator'] ?? '')),
                esc_html(implode('; ', $products)),
                esc_html((string) ($record['treated_amount'] ?? ''))
            );
        }
        $body .= '</tbody></table>';

        return [
            'title'       => sprintf('%s — %s', (string) $application['title'], $when),
            'description' => sprintf(
                /* translators: 1: title, 2: acres, 3: date range, 4: county */
                __('%1$s: %2$s acres treated %3$s in %4$s County, California.', 'ground-truth-tracker'),
                (string) $application['title'],
                $acres,
                $when,
                (string) ($application['county'] ?? '')
            ),
            'canonical'   => GT_Router::url('application', $slug),
            'og_type'     => 'article',
            'schema'      => [
                '@context'    => 'https://schema.org',
                '@type'       => 'Dataset',
                'name'        => (string) $application['title'],
                'description' => sprintf(
                    __('Pesticide use reports for %s.', 'ground-truth-tracker'),
                    (string) $application['title']
                ),
                'url'         => GT_Router::url('application', $slug),
                'temporalCoverage' => trim((string) ($application['date_start'] ?? '') . '/' . (string) ($application['date_end'] ?? '')),
                'creator'     => ['@type' => 'Organization', 'name' => 'Ground Truth'],
                'isBasedOn'   => __('California pesticide use reports obtained from county agricultural commissioners', 'ground-truth-tracker'),
            ],
            'body'        => $body,
        ];
    }

    private function chemicals_page(): ?array
    {
        $data = GT_Client::get('/api/chemicals');
        if (is_wp_error($data)) {
            return $this->error_page($data);
        }
        $body = '<h1>' . esc_html__('Chemicals', 'ground-truth-tracker') . '</h1><ul>';
        foreach ($data['chemicals'] ?? [] as $chemical) {
            $body .= sprintf(
                '<li><a href="%s">%s</a>%s%s</li>',
                esc_url(GT_Router::url('chemical', (string) $chemical['slug'])),
                esc_html((string) $chemical['name']),
                !empty($chemical['is_california_restricted'])
                    ? ' <span class="gt-badge gt-red">' . esc_html__('California Restricted Material', 'ground-truth-tracker') . '</span>'
                    : '',
                !empty($chemical['is_watchlisted'])
                    ? ' <span class="gt-badge gt-red">' . esc_html__('Ground Truth Watchlist', 'ground-truth-tracker') . '</span>'
                    : ''
            );
        }
        $body .= '</ul>';

        return [
            'title'       => __('Chemicals', 'ground-truth-tracker'),
            'description' => __('Active ingredients applied in the forestry herbicide applications this tracker covers.', 'ground-truth-tracker'),
            'canonical'   => GT_Router::url('chemicals'),
            'og_type'     => 'website',
            'schema'      => [],
            'body'        => $body,
        ];
    }

    private function chemical_page(string $slug): ?array
    {
        $chemical = GT_Client::get('/api/chemicals/' . rawurlencode($slug));
        if (is_wp_error($chemical)) {
            $status = $chemical->get_error_data()['status'] ?? 0;
            return $status === 404 ? null : $this->error_page($chemical);
        }

        $body = sprintf('<h1>%s</h1>', esc_html((string) $chemical['name']));
        foreach ($chemical['flags'] ?? [] as $flag) {
            $body .= sprintf(
                '<div class="gt-flag gt-flag-%s"><strong>%s</strong><div>%s</div><div class="gt-src">%s</div></div>',
                esc_attr((string) $flag['level']),
                esc_html((string) $flag['label']),
                esc_html((string) $flag['detail']),
                esc_html((string) $flag['source'])
            );
        }
        foreach (['overview', 'groundwater', 'surface_water', 'persistence', 'ecological', 'human_health'] as $key) {
            $text = $chemical['sections'][$key] ?? null;
            if ($text) {
                $body .= sprintf('<h2>%s</h2><p>%s</p>', esc_html(ucwords(str_replace('_', ' ', $key))), esc_html((string) $text));
            }
        }
        $body .= '<h2>' . esc_html__('Applications using this chemical', 'ground-truth-tracker') . '</h2>';
        $body .= $this->grid($chemical['applications'] ?? []);

        return [
            'title'       => (string) $chemical['name'],
            'description' => sprintf(
                __('%s in California forestry herbicide applications: regulatory status and every tracked application that used it.', 'ground-truth-tracker'),
                (string) $chemical['name']
            ),
            'canonical'   => GT_Router::url('chemical', $slug),
            'og_type'     => 'article',
            'schema'      => [],
            'body'        => $body,
        ];
    }

    private function near_page(): ?array
    {
        $address = isset($_GET['address']) ? sanitize_text_field(wp_unslash((string) $_GET['address'])) : '';
        $miles = isset($_GET['miles']) ? (float) $_GET['miles'] : 1.0;

        $body = '<h1>' . esc_html__('Search near an address', 'ground-truth-tracker') . '</h1>';
        $body .= sprintf(
            '<form method="get" action="%s" class="gt-form">
                <label for="gt-address">%s</label>
                <input id="gt-address" type="text" name="address" value="%s" required />
                <label for="gt-miles">%s</label>
                <select id="gt-miles" name="miles">%s</select>
                <button type="submit">%s</button>
            </form>',
            esc_url(GT_Router::url('near')),
            esc_html__('Address', 'ground-truth-tracker'),
            esc_attr($address),
            esc_html__('Radius', 'ground-truth-tracker'),
            implode('', array_map(
                static fn($value) => sprintf(
                    '<option value="%1$s"%2$s>%1$s mi</option>',
                    esc_attr((string) $value),
                    selected($miles, $value, false)
                ),
                [0.5, 1, 2, 5, 10, 25]
            )),
            esc_html__('Search', 'ground-truth-tracker')
        );
        $body .= '<p class="gt-muted gt-small">' . esc_html__(
            'The address you enter is used for this search only. It is not stored.',
            'ground-truth-tracker'
        ) . '</p>';

        if ($address !== '') {
            // Never cached: the query contains a member of the public's address.
            $results = GT_Client::get('/api/search/radius', ['address' => $address, 'miles' => $miles], 0);
            if (is_wp_error($results)) {
                $body .= '<p class="gt-notice gt-warn">' . esc_html__(
                    'That address could not be located.',
                    'ground-truth-tracker'
                ) . '</p>';
            } else {
                $body .= sprintf(
                    '<h2>%s</h2>',
                    esc_html(sprintf(
                        /* translators: 1: count, 2: miles */
                        __('%1$d applications within %2$s miles', 'ground-truth-tracker'),
                        (int) $results['count'],
                        (string) $results['radius_miles']
                    ))
                );
                $body .= '<ul>';
                foreach ($results['results'] ?? [] as $row) {
                    $body .= sprintf(
                        '<li><a href="%s">%s</a> — %s mi — %s</li>',
                        esc_url(GT_Router::url('application', (string) $row['slug'])),
                        esc_html((string) $row['title']),
                        esc_html((string) $row['distance_miles']),
                        esc_html((string) ($row['date'] ?? ''))
                    );
                }
                $body .= '</ul>';
            }
        }

        return [
            'title'       => __('Search near an address', 'ground-truth-tracker'),
            'description' => __('Find forestry herbicide applications within a chosen distance of an address.', 'ground-truth-tracker'),
            'canonical'   => GT_Router::url('near'),
            'og_type'     => 'website',
            'schema'      => [],
            'body'        => $body,
        ];
    }

    /** @param array<int, array<string, mixed>> $rows */
    public function grid(array $rows): string
    {
        if ($rows === []) {
            return '<p class="gt-muted">' . esc_html__('No applications to show.', 'ground-truth-tracker') . '</p>';
        }

        $html = '<table class="gt-grid"><thead><tr>';
        foreach ([
            __('Date', 'ground-truth-tracker'),
            __('Project / property', 'ground-truth-tracker'),
            __('Acres', 'ground-truth-tracker'),
            __('Chemicals', 'ground-truth-tracker'),
            __('Method', 'ground-truth-tracker'),
        ] as $heading) {
            $html .= '<th>' . esc_html($heading) . '</th>';
        }
        $html .= '</tr></thead><tbody>';

        foreach ($rows as $row) {
            $url = GT_Router::url('application', (string) $row['slug']);
            $flag = '';
            if (!empty($row['flag_headline'])) {
                $flag = sprintf(
                    '<span class="gt-badge gt-%s">%s</span>',
                    esc_attr((string) $row['flag_level']),
                    esc_html(preg_replace('/^\w+\s*—\s*/u', '', (string) $row['flag_headline']) ?? '')
                );
            }
            $html .= sprintf(
                '<tr class="%s">
                    <td><a href="%s">%s</a></td>
                    <td><a href="%s"><strong>%s</strong>%s</a></td>
                    <td class="gt-num"><a href="%s">%s</a></td>
                    <td><a href="%s">%s %s</a></td>
                    <td><a href="%s">%s</a></td>
                </tr>',
                ($row['flag_level'] ?? '') === 'red' ? 'gt-row-red' : '',
                esc_url($url),
                esc_html($this->date_range($row['date_start'] ?? null, $row['date_end'] ?? null)),
                esc_url($url),
                esc_html((string) $row['title']),
                (int) ($row['record_count'] ?? 1) > 1
                    ? '<div class="gt-muted gt-small">' . esc_html(sprintf(
                        /* translators: %d: number of reports */
                        __('%d pesticide use reports', 'ground-truth-tracker'),
                        (int) $row['record_count']
                    )) . '</div>'
                    : '',
                esc_url($url),
                esc_html($row['acres'] !== null ? number_format_i18n((float) $row['acres'], 1) : '—'),
                esc_url($url),
                esc_html(implode(', ', array_slice((array) ($row['chemicals'] ?? []), 0, 3))),
                $flag,
                esc_url($url),
                esc_html(($row['method'] ?? '') === 'aerial'
                    ? __('Aerial', 'ground-truth-tracker')
                    : __('Ground', 'ground-truth-tracker'))
            );
        }
        return $html . '</tbody></table>';
    }

    private function fact(string $label, string $value): string
    {
        return sprintf(
            '<dt>%s</dt><dd>%s</dd>',
            esc_html($label),
            esc_html($value !== '' ? $value : __('Not reported', 'ground-truth-tracker'))
        );
    }

    private function date_range(?string $start, ?string $end): string
    {
        if (!$start) {
            return __('Date not reported', 'ground-truth-tracker');
        }
        $format = (string) get_option('date_format', 'M j, Y');
        $from = date_i18n($format, strtotime($start));
        if (!$end || $end === $start) {
            return $from;
        }
        return $from . ' – ' . date_i18n($format, strtotime($end));
    }

    /** @return array{title:string,description:string,canonical:string,og_type:string,schema:array<string,mixed>,body:string} */
    private function error_page(WP_Error $error): array
    {
        return [
            'title'       => __('Herbicide tracker unavailable', 'ground-truth-tracker'),
            'description' => __('The herbicide tracker is temporarily unavailable.', 'ground-truth-tracker'),
            'canonical'   => GT_Router::url('index'),
            'og_type'     => 'website',
            'schema'      => [],
            'body'        => '<h1>' . esc_html__('Temporarily unavailable', 'ground-truth-tracker') . '</h1>'
                . '<p>' . esc_html__('The tracker could not be reached. Please try again shortly.', 'ground-truth-tracker') . '</p>'
                . (current_user_can('manage_options')
                    ? '<p class="gt-muted gt-small">' . esc_html($error->get_error_message()) . '</p>'
                    : ''),
        ];
    }
}
