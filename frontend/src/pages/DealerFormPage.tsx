import { useEffect, useMemo, useState } from 'react';
import { Link as RouterLink, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  CircularProgress,
  FormControl,
  FormControlLabel,
  IconButton,
  InputAdornment,
  InputLabel,
  LinearProgress,
  MenuItem,
  Select,
  Skeleton,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import AutoAwesomeRoundedIcon from '@mui/icons-material/AutoAwesomeRounded';
import ArrowBackRoundedIcon from '@mui/icons-material/ArrowBackRounded';
import LanguageRoundedIcon from '@mui/icons-material/LanguageRounded';
import BadgeRoundedIcon from '@mui/icons-material/BadgeRounded';
import CheckCircleRoundedIcon from '@mui/icons-material/CheckCircleRounded';
import ErrorRoundedIcon from '@mui/icons-material/ErrorRounded';
import WarningAmberRoundedIcon from '@mui/icons-material/WarningAmberRounded';
import ContentPasteRoundedIcon from '@mui/icons-material/ContentPasteRounded';
import { useSnackbar } from 'notistack';
import StopCircleRoundedIcon from '@mui/icons-material/StopCircleRounded';
import {
  cancelDiscoveryRun,
  createDealer,
  discoverConfig,
  type DiscoveryProgress,
  getDealer,
  listDealers,
  listProviders,
  updateDealer,
} from '../api/dealers';
import { getErrorMessage } from '../api/client';
import { hasForeignUrls, retargetConfig } from '../utils/retargetConfig';
import { PageHeader, SectionCard, Surface } from '../components/ui';
import { useTokens } from '../theme/useTokens';
import type { DealerCreate, Discovery } from '../types';

const EMPTY: DealerCreate = {
  name: '',
  base_url: '',
  address: '',
  city: '',
  province: '',
  postal_code: '',
  phone: '',
  website_provider: '',
  active: true,
  adapter_mode: 'browser',
  scrape_config: {},
};

// Mirrors REQUIRED_CONFIG on the adapter classes (dealer_scraper/adapters/*.py).
// The API enforces this too; checking here just saves a round trip.
const REQUIRED_CONFIG_KEYS: Record<string, string[]> = {
  browser: ['list_url'],
  json_api: ['endpoint', 'field_map'],
};

const CONFIG_TEMPLATE: Record<string, string> = {
  browser: JSON.stringify(
    {
      list_url: 'https://example.com/inventory',
      base_url: 'https://example.com',
      wait_selector: "a[href^='/vehicles/']",
      card_anchor: "a[href^='/vehicles/']",
      id_regex: '/vehicles/(\\d+)',
      paginate: 'scroll',
      scroll_rounds: 40,
      max_pages: 60,
    },
    null,
    2,
  ),
  json_api: JSON.stringify(
    {
      endpoint: 'https://api.example.com/v1/vehicles?page={page}&limit={page_size}',
      page_size: 25,
      list_path: 'results.vehicles',
      total_path: 'results.total',
      field_map: { external_id: 'id', vin: 'vin', make: 'make', model: 'model', price: 'price' },
    },
    null,
    2,
  ),
};

function isValidUrl(value: string): boolean {
  try {
    const u = new URL(value);
    return u.protocol === 'http:' || u.protocol === 'https:';
  } catch {
    return false;
  }
}

export function DealerFormPage() {
  const { id } = useParams();
  const isEdit = Boolean(id);
  const navigate = useNavigate();
  const theme = useTheme();
  const tk = useTokens();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();

  const [form, setForm] = useState<DealerCreate>(EMPTY);
  const [configText, setConfigText] = useState('{}');
  const [configError, setConfigError] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const [scraperReused, setScraperReused] = useState(false);
  const [reusedFrom, setReusedFrom] = useState<string | null>(null);
  // The template dealer's own site, so the warning below can tell its URLs
  // apart from a shared vendor host that is fine to keep.
  const [reusedFromUrl, setReusedFromUrl] = useState<string | null>(null);
  const [discovery, setDiscovery] = useState<Discovery | null>(null);
  // Set when the API refuses to onboard a site the scraper could not read.
  const [blockedReason, setBlockedReason] = useState<string | null>(null);
  // Deliberate override of that refusal — never defaulted, only set by an
  // explicit click, so an unreadable site can't be onboarded by accident.
  const [forceAdd, setForceAdd] = useState(false);
  const [discoveryProgress, setDiscoveryProgress] = useState<DiscoveryProgress | null>(null);
  const [discoveryRunId, setDiscoveryRunId] = useState<number | null>(null);
  const [cancellingDiscovery, setCancellingDiscovery] = useState(false);

  const discoverMut = useMutation({
    mutationFn: (url: string) =>
      discoverConfig(url, (progress) => {
        setDiscoveryProgress(progress);
        setDiscoveryRunId(progress.runId);
      }),
    onSuccess: (result) => {
      setDiscovery(result);
      setScraperReused(false);
      if (result.ok && Object.keys(result.scrape_config).length > 0) {
        setField('scrape_config', result.scrape_config);
        setField('adapter_mode', result.adapter_mode);
        setConfigText(JSON.stringify(result.scrape_config, null, 2));
        setConfigError(null);
        enqueueSnackbar(`Detected ${result.vehicles_found} vehicles — settings filled in`, {
          variant: result.requires_custom_work ? 'warning' : 'success',
        });
      } else {
        enqueueSnackbar('Could not detect settings for this website', { variant: 'warning' });
      }
    },
    onError: (e) => {
      const msg = getErrorMessage(e);
      if (msg.toLowerCase().includes('cancelled')) {
        enqueueSnackbar(msg, { variant: 'info' });
        return;
      }
      enqueueSnackbar(msg, { variant: 'error' });
    },
    onSettled: () => {
      setDiscoveryProgress(null);
      setDiscoveryRunId(null);
      setCancellingDiscovery(false);
    },
  });

  async function handleCancelDiscovery() {
    if (discoveryRunId == null) return;
    setCancellingDiscovery(true);
    try {
      const res = await cancelDiscoveryRun(discoveryRunId);
      enqueueSnackbar(res.detail, { variant: 'info' });
    } catch (e) {
      enqueueSnackbar(getErrorMessage(e), { variant: 'error' });
      setCancellingDiscovery(false);
    }
  }

  function handleDetect() {
    const url = form.base_url.trim();
    if (!url || !isValidUrl(url)) {
      enqueueSnackbar('Enter a valid website URL first', { variant: 'warning' });
      return;
    }
    setDiscovery(null);
    discoverMut.mutate(url);
  }

  const dealerQ = useQuery({ queryKey: ['dealer', id], queryFn: () => getDealer(Number(id)), enabled: isEdit });
  const providersQ = useQuery({ queryKey: ['providers'], queryFn: listProviders });
  const dealersQ = useQuery({ queryKey: ['dealers', 'templates'], queryFn: () => listDealers() });

  useEffect(() => {
    if (dealerQ.data) {
      const d = dealerQ.data;
      setForm({
        name: d.name,
        base_url: d.base_url,
        address: d.address ?? '',
        city: d.city ?? '',
        province: d.province ?? '',
        postal_code: d.postal_code ?? '',
        phone: d.phone ?? '',
        website_provider: d.website_provider ?? '',
        active: d.active,
        adapter_mode: d.adapter_mode,
        scrape_config: d.scrape_config ?? {},
      });
      setConfigText(JSON.stringify(d.scrape_config ?? {}, null, 2));
    }
  }, [dealerQ.data]);

  const providerOptions = useMemo(() => {
    const fromApi = providersQ.data ?? [];
    const fromDealers = (dealersQ.data ?? []).map((d) => d.website_provider).filter((p): p is string => Boolean(p));
    return Array.from(new Set([...fromApi, ...fromDealers])).sort();
  }, [providersQ.data, dealersQ.data]);

  function setField<K extends keyof DealerCreate>(key: K, value: DealerCreate[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function onProviderChange(value: string | null) {
    const provider = value ?? '';
    setField('website_provider', provider);
    if (!provider || isEdit) {
      setScraperReused(false);
      return;
    }
    // Prefer a template that has actually scraped something: copying a config
    // that never produced a vehicle spreads a broken template across the fleet.
    const candidates = (dealersQ.data ?? []).filter(
      (d) => d.website_provider === provider && d.scrape_config &&
             Object.keys(d.scrape_config).length > 0,
    );
    const template =
      candidates.find((d) => d.inventory_count > 0) ?? candidates[0];
    if (template) {
      // Repoint the copied URLs at THIS dealer's site. Left verbatim they point
      // at the template dealer, so the new dealership would silently scrape the
      // other dealer's inventory under its own name.
      const config = retargetConfig(
        template.scrape_config,
        template.base_url,
        form.base_url.trim(),
      );
      setField('scrape_config', config);
      setField('adapter_mode', template.adapter_mode);
      setConfigText(JSON.stringify(config, null, 2));
      setReusedFrom(template.name);
      setReusedFromUrl(template.base_url);
      setScraperReused(true);
    } else {
      setReusedFrom(null);
      setReusedFromUrl(null);
      setScraperReused(false);
    }
  }

  function parseConfig(): Record<string, unknown> | null {
    try {
      const parsed = JSON.parse(configText || '{}') as unknown;
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        setConfigError('Scrape config must be a JSON object');
        return null;
      }
      setConfigError(null);
      return parsed as Record<string, unknown>;
    } catch {
      setConfigError('Invalid JSON');
      return null;
    }
  }

  const errors = useMemo(() => {
    const e: Record<string, string> = {};
    if (!form.name.trim()) e.name = 'Required';
    else if (form.name.length > 200) e.name = 'Max 200 characters';
    if (!form.base_url.trim()) e.base_url = 'Required';
    else if (form.base_url.length > 500) e.base_url = 'Max 500 characters';
    else if (!isValidUrl(form.base_url.trim())) e.base_url = 'Enter a valid URL';
    return e;
  }, [form]);

  // A dealer saved with an empty config is accepted by every field-level check
  // and then fails at sync time, so require the adapter's keys before saving.
  const missingConfigKeys = useMemo(() => {
    const required = REQUIRED_CONFIG_KEYS[form.adapter_mode ?? 'browser'] ?? [];
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(configText || '{}') as Record<string, unknown>;
    } catch {
      return [];
    }
    return required.filter((k) => {
      const v = parsed?.[k];
      return (
        v === undefined ||
        v === null ||
        v === '' ||
        (typeof v === 'object' && Object.keys(v as object).length === 0)
      );
    });
  }, [configText, form.adapter_mode]);

  // A reused config whose URLs still name another dealer's host is the one way
  // platform reuse can quietly corrupt an onboarding, so it is surfaced rather
  // than left to a careful reader.
  const configPointsElsewhere = useMemo(() => {
    if (!scraperReused || !form.base_url.trim()) return false;
    try {
      return hasForeignUrls(JSON.parse(configText || '{}'), form.base_url.trim(), reusedFromUrl);
    } catch {
      return false;
    }
  }, [scraperReused, configText, form.base_url, reusedFromUrl]);

  const saveM = useMutation({
    mutationFn: async () => {
      const scrape_config = parseConfig();
      if (!scrape_config) throw new Error(configError || 'Invalid scrape config');
      if (missingConfigKeys.length) {
        throw new Error(
          `Scrape config is missing required ${missingConfigKeys.length > 1 ? 'keys' : 'key'}: ` +
            `${missingConfigKeys.join(', ')} — open "Scrape configuration" below to add ${
              missingConfigKeys.length > 1 ? 'them' : 'it'
            }.`,
        );
      }
      if (Object.keys(errors).length) throw new Error('Fix validation errors');
      const payload: DealerCreate = {
        ...form,
        name: form.name.trim(),
        base_url: form.base_url.trim().replace(/\/$/, ''),
        website_provider: form.website_provider?.trim() || null,
        address: form.address?.trim() || null,
        city: form.city?.trim() || null,
        province: form.province?.trim() || null,
        postal_code: form.postal_code?.trim() || null,
        phone: form.phone?.trim() || null,
        scrape_config,
      };
      if (isEdit) return updateDealer(Number(id), payload);
      return createDealer({ ...payload, force: forceAdd });
    },
    onSuccess: (dealer) => {
      enqueueSnackbar(isEdit ? 'Dealership updated' : 'Dealership created', { variant: 'success' });
      qc.invalidateQueries({ queryKey: ['dealers'] });
      navigate(`/dealers/${dealer.id}`);
    },
    onError: (e) => {
      // A 409 here is the proven-site gate refusing an unreadable website. That
      // is the single most important thing to explain, so it gets a persistent
      // panel with the option to override — not a snackbar that vanishes.
      if (isAxiosError(e) && e.response?.status === 409) {
        setBlockedReason(getErrorMessage(e));
        return;
      }
      enqueueSnackbar(getErrorMessage(e), { variant: 'error' });
    },
  });

  if (isEdit && dealerQ.isLoading) {
    return (
      <Box sx={{ maxWidth: tk.layout.formWidth }}>
        <Skeleton width={220} height={40} sx={{ mb: 3 }} />
        <Skeleton variant="rectangular" height={360} sx={{ borderRadius: 4, mb: 2 }} />
        <Skeleton variant="rectangular" height={220} sx={{ borderRadius: 4 }} />
      </Box>
    );
  }

  const cancelHref = isEdit ? `/dealers/${id}` : '/dealers';

  return (
    <Box sx={{ maxWidth: tk.layout.formWidth }}>
      <PageHeader
        leading={
          <Tooltip title="Cancel">
            <IconButton component={RouterLink} to={cancelHref} aria-label="Cancel" sx={{ mt: 0.5 }}>
              <ArrowBackRoundedIcon />
            </IconButton>
          </Tooltip>
        }
        title={isEdit ? 'Edit dealership' : 'Add dealership'}
        description={
          isEdit
            ? 'Update this source’s details or scraper configuration.'
            : 'Point the platform at a dealership website — settings can be auto-detected from the URL alone.'
        }
      />

      {scraperReused && (
        <Alert severity={configPointsElsewhere ? 'warning' : 'info'} sx={{ mb: 2 }}>
          {configPointsElsewhere ? (
            <>
              Settings were reused from{reusedFrom ? ` ${reusedFrom}` : ' another dealership'}, but some
              URLs below still point at a different website. Fix them before saving, or this dealership
              will scrape the wrong inventory.
            </>
          ) : (
            <>
              No new scraper needed — this platform is already supported
              {reusedFrom ? `, so the configuration proven on ${reusedFrom} was reused` : ''} and
              repointed at this dealership's own website.
            </>
          )}
        </Alert>
      )}

      {blockedReason && (
        <Alert
          severity="error"
          sx={{ mb: 2 }}
          action={
            <Button
              color="inherit"
              size="small"
              onClick={() => {
                setForceAdd(true);
                setBlockedReason(null);
                enqueueSnackbar('Override enabled — press Save again to add anyway', {
                  variant: 'warning',
                });
              }}
            >
              Add anyway
            </Button>
          }
        >
          <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
            This website cannot be scraped yet
          </Typography>
          <Typography variant="body2" sx={{ mt: 0.5 }}>
            {blockedReason}
          </Typography>
        </Alert>
      )}

      {forceAdd && !blockedReason && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          Override active — this dealership will be added even though the scraper could
          not verify its website. It may stay at zero inventory until a custom scraper
          is built for it.
        </Alert>
      )}

      {discovery && <DiscoveryBanner discovery={discovery} />}

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pb: 12 }}>
        <SectionCard title="Basics" description="How this dealership appears across the platform">
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.5, pt: 0.5 }}>
            <TextField
              label="Dealership name"
              required
              value={form.name}
              onChange={(e) => setField('name', e.target.value)}
              error={touched && !!errors.name}
              helperText={touched ? errors.name : undefined}
              slotProps={{
                htmlInput: { maxLength: 200 },
                input: {
                  startAdornment: (
                    <InputAdornment position="start">
                      <BadgeRoundedIcon sx={{ fontSize: 18, color: 'text.disabled' }} />
                    </InputAdornment>
                  ),
                },
              }}
            />

            <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start', flexDirection: { xs: 'column', sm: 'row' } }}>
              <TextField
                label="Website URL"
                required
                fullWidth
                value={form.base_url}
                onChange={(e) => setField('base_url', e.target.value)}
                error={touched && !!errors.base_url}
                helperText={
                  touched && errors.base_url ? errors.base_url : 'Then press Detect settings to auto-configure the scraper'
                }
                slotProps={{
                  htmlInput: { maxLength: 500 },
                  input: {
                    startAdornment: (
                      <InputAdornment position="start">
                        <LanguageRoundedIcon sx={{ fontSize: 18, color: 'text.disabled' }} />
                      </InputAdornment>
                    ),
                  },
                }}
              />
              <Button
                variant="contained"
                onClick={handleDetect}
                disabled={discoverMut.isPending}
                sx={{ whiteSpace: 'nowrap', minWidth: 168, height: 40, flexShrink: 0 }}
                startIcon={
                  discoverMut.isPending ? <CircularProgress size={16} color="inherit" /> : <AutoAwesomeRoundedIcon />
                }
              >
                {discoverMut.isPending ? 'Detecting…' : 'Detect settings'}
              </Button>
            </Box>

            {discoverMut.isPending && (
              <Alert
                severity="info"
                icon={<CircularProgress size={18} />}
                action={
                  discoveryRunId != null ? (
                    <Button
                      color="inherit"
                      size="small"
                      onClick={handleCancelDiscovery}
                      disabled={cancellingDiscovery}
                      startIcon={
                        cancellingDiscovery ? (
                          <CircularProgress size={14} color="inherit" />
                        ) : (
                          <StopCircleRoundedIcon fontSize="small" />
                        )
                      }
                    >
                      Stop
                    </Button>
                  ) : undefined
                }
              >
                <Box sx={{ width: '100%' }}>
                  <Typography variant="body2" sx={{ mb: discoveryProgress ? 1 : 0 }}>
                    {discoveryProgressLabel(discoveryProgress)}
                  </Typography>
                  {discoveryProgress && discoveryProgress.phasesTotal > 0 && (
                    <LinearProgress
                      variant="determinate"
                      value={Math.min(
                        100,
                        (discoveryProgress.phasesDone / discoveryProgress.phasesTotal) * 100,
                      )}
                      sx={{ borderRadius: 1, height: 6 }}
                    />
                  )}
                </Box>
              </Alert>
            )}

            <Autocomplete
              freeSolo
              options={providerOptions}
              value={form.website_provider ?? ''}
              onChange={(_, value) => onProviderChange(typeof value === 'string' ? value : null)}
              onInputChange={(_, value) => onProviderChange(value)}
              renderInput={(params) => <TextField {...params} label="Website provider" placeholder="e.g. Clutch" />}
            />

            <FormControlLabel
              control={<Switch checked={!!form.active} onChange={(e) => setField('active', e.target.checked)} />}
              label={
                <Box>
                  <Typography variant="body2" sx={{ fontWeight: 560 }}>
                    Active
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    Inactive dealerships are excluded from bulk syncs
                  </Typography>
                </Box>
              }
              sx={{ ml: 0, gap: 1 }}
            />
          </Box>
        </SectionCard>

        <SectionCard title="Location & contact" description="Optional — shown on the dealership's detail page">
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.5, pt: 0.5 }}>
            <TextField label="Phone" value={form.phone ?? ''} onChange={(e) => setField('phone', e.target.value)} />
            <TextField label="Address" value={form.address ?? ''} onChange={(e) => setField('address', e.target.value)} />
            <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
              <TextField
                label="City"
                value={form.city ?? ''}
                onChange={(e) => setField('city', e.target.value)}
                sx={{ flex: 1, minWidth: 140 }}
              />
              <TextField
                label="Province"
                value={form.province ?? ''}
                onChange={(e) => setField('province', e.target.value)}
                sx={{ width: 120 }}
              />
              <TextField
                label="Postal code"
                value={form.postal_code ?? ''}
                onChange={(e) => setField('postal_code', e.target.value)}
                sx={{ width: 140 }}
              />
            </Box>
          </Box>
        </SectionCard>

        <SectionCard
          title="Scrape configuration"
          description="Advanced — usually filled automatically by Detect settings"
          actions={
            missingConfigKeys.length > 0 ? (
              <Button
                size="small"
                variant="outlined"
                color="warning"
                disabled={discoverMut.isPending}
                startIcon={<ContentPasteRoundedIcon sx={{ fontSize: 16 }} />}
                onClick={() => {
                  setConfigText(CONFIG_TEMPLATE[form.adapter_mode ?? 'browser'] ?? '{}');
                  setConfigError(null);
                }}
              >
                Use template
              </Button>
            ) : undefined
          }
        >
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.5, pt: 0.5 }}>
            <FormControl>
              <InputLabel>Adapter mode</InputLabel>
              <Select label="Adapter mode" value={form.adapter_mode ?? 'browser'} onChange={(e) => setField('adapter_mode', e.target.value)}>
                <MenuItem value="browser">browser — renders the site like a visitor would</MenuItem>
                <MenuItem value="json_api">json_api — calls a structured inventory endpoint</MenuItem>
              </Select>
            </FormControl>

            {missingConfigKeys.length > 0 && (
              <Alert severity="warning" icon={<WarningAmberRoundedIcon />}>
                {`Missing required ${missingConfigKeys.length > 1 ? 'keys' : 'key'} for `}
                <strong>{form.adapter_mode ?? 'browser'}</strong>
                {` mode: ${missingConfigKeys.join(', ')}. Without ${
                  missingConfigKeys.length > 1 ? 'these' : 'this'
                }, syncing this dealership will fail.`}
              </Alert>
            )}

            <TextField
              label="Scrape config (JSON)"
              multiline
              minRows={10}
              fullWidth
              value={configText}
              onChange={(e) => {
                setConfigText(e.target.value);
                setConfigError(null);
              }}
              error={!!configError}
              helperText={configError}
              slotProps={{
                input: {
                  sx: {
                    fontFamily: 'ui-monospace, "Geist Mono", monospace',
                    fontSize: 13,
                    bgcolor: tk.surfaces.sunken,
                    borderRadius: `${tk.radii.control}px`,
                  },
                },
              }}
            />
          </Box>
        </SectionCard>
      </Box>

      {/* ── Sticky save bar ────────────────────────────────────────────── */}
      <Surface
        tone="raised"
        radius={tk.radii.control}
        sx={{
          position: 'sticky',
          bottom: 16,
          display: 'flex',
          alignItems: 'center',
          gap: 1.5,
          p: 1.5,
          zIndex: 2,
          backdropFilter: 'blur(10px)',
          backgroundColor: alpha(theme.palette.background.paper, 0.92),
        }}
      >
        <Tooltip
          title={
            discovery?.verdict === 'unsupported'
              ? 'This website is not supported. It requires custom scraper development before it can be enabled.'
              : ''
          }
        >
          <Button
            variant="contained"
            disabled={
              saveM.isPending ||
              discoverMut.isPending ||
              discovery?.verdict === 'unsupported' ||
              (Object.keys(errors).length > 0 && touched)
            }
            startIcon={saveM.isPending ? <CircularProgress size={16} color="inherit" /> : undefined}
            onClick={() => {
              setTouched(true);
              saveM.mutate();
            }}
          >
            {isEdit ? 'Save changes' : 'Create dealership'}
          </Button>
        </Tooltip>
        <Button component={RouterLink} to={cancelHref} disabled={discoverMut.isPending}>
          Cancel
        </Button>
        {discovery?.verdict === 'unsupported' && (
          <Typography variant="caption" color="error.main" sx={{ ml: 'auto' }}>
            Blocked: website requires custom development
          </Typography>
        )}
        {Object.keys(errors).length > 0 && touched && !discovery?.verdict && (
          <Typography variant="caption" color="error.main" sx={{ ml: 'auto' }}>
            Fix the highlighted fields to continue
          </Typography>
        )}
      </Surface>
    </Box>
  );
}

const DISCOVERY_PHASE_LABELS: Record<string, string> = {
  explore: 'Opening homepage…',
  candidates: 'Scanning inventory pages…',
  pagination: 'Detecting pagination and data source…',
  done: 'Generating configuration…',
  queued: 'Waiting to start…',
  running: 'Running discovery…',
};

function discoveryProgressLabel(progress: DiscoveryProgress | null): string {
  if (!progress) return 'Starting discovery…';
  if (progress.status === 'queued') return DISCOVERY_PHASE_LABELS.queued;
  const phase = progress.phase ?? progress.status;
  return DISCOVERY_PHASE_LABELS[phase] ?? 'Analyzing website…';
}

/** Discovery result banner — mirrors the verdict semantics from the API. */
function DiscoveryBanner({ discovery }: { discovery: Discovery }) {
  const severity = discovery.verdict === 'ready' ? 'success' : discovery.verdict === 'unsupported' ? 'error' : 'warning';
  const Icon = discovery.verdict === 'ready' ? CheckCircleRoundedIcon : discovery.verdict === 'unsupported' ? ErrorRoundedIcon : WarningAmberRoundedIcon;

  const actionText =
    discovery.verdict === 'ready'
      ? '✓ You can now save and enable this dealership'
      : discovery.verdict === 'unsupported'
        ? '✗ This website requires custom scraper development — contact sales'
        : '⚠ Review one scrape run before enabling automatic syncs';

  return (
    <Alert severity={severity} icon={<Icon />} sx={{ mb: 2 }}>
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
        {discovery.verdict === 'ready'
          ? 'Website supported'
          : discovery.verdict === 'unsupported'
            ? 'Website not supported yet'
            : 'Needs review'}
      </Typography>
      <Typography variant="body2" sx={{ mt: 0.5 }}>
        {discovery.summary}
      </Typography>
      {discovery.ok && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
          {discovery.vehicles_found} vehicles found
          {discovery.listing_url ? ` on ${discovery.listing_url}` : ''}
          {discovery.pagination ? ` · pagination: ${discovery.pagination}` : ''}
          {discovery.extract ? ` · data: ${discovery.extract}` : ''}
        </Typography>
      )}
      {discovery.report?.phases && discovery.report.phases.length > 0 && (
        <Box sx={{ mt: 1.5 }}>
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
            Discovery timing
            {discovery.report.duration_seconds != null
              ? ` · ${discovery.report.duration_seconds.toFixed(1)}s total`
              : ''}
          </Typography>
          <Box component="ul" sx={{ m: 0, mt: 0.5, pl: 2.25 }}>
            {discovery.report.phases.map((phase) => (
              <Typography
                key={phase.name}
                component="li"
                variant="caption"
                color="text.secondary"
                sx={{ lineHeight: 1.6 }}
              >
                {phase.name}: {phase.duration_seconds.toFixed(1)}s
              </Typography>
            ))}
          </Box>
        </Box>
      )}
      <Typography variant="body2" sx={{ mt: 1, fontWeight: 600 }}>
        {actionText}
      </Typography>
      {discovery.requires_custom_work && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
          Custom development required: API incompatible or inventory structure too complex
        </Typography>
      )}
    </Alert>
  );
}
