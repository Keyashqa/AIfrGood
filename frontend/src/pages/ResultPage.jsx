import { useState, useEffect } from 'react';
import {
  Box, Tabs, Tab, Typography, Paper, TextField, Button, Stack,
  Divider, Snackbar, Alert, CircularProgress, Chip, List, ListItem,
  ListItemText, ListItemIcon, Table, TableHead, TableRow, TableCell,
  TableBody, TableContainer,
} from '@mui/material';
import {
  Download, Save, CheckBoxOutlineBlank, MedicalServices,
  AccessTime, WarningAmber,
} from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import useTriageStore from '../state/triageStore';
import VerdictHeader from '../components/result/VerdictHeader';
import SafetyAlerts from '../components/result/SafetyAlerts';
import ExplanationCard from '../components/result/ExplanationCard';
import DepartmentRouting from '../components/result/DepartmentRouting';
import WorkupTable from '../components/result/WorkupTable';
import CouncilSummary from '../components/result/CouncilSummary';
import OtherDepartments from '../components/result/OtherDepartments';
import { saveDoctorNotes, downloadReport, getDoctorNotes } from '../api/triageApi';
import { RISK_COLORS } from '../utils/constants';

// Urgency gradient: Immediate (deep red) → 1hr (deep orange) → 4hrs (amber) → Elective (green)
const URGENCY_CONFIG = {
  IMMEDIATE:  { color: '#B71C1C', label: 'IMMEDIATE — Call NOW',   bg: '#FFEBEE' },
  WITHIN_1HR: { color: '#E65100', label: 'Within 1 Hour',          bg: '#FFF3E0' },
  WITHIN_4HRS:{ color: '#F9A825', label: 'Within 4 Hours',         bg: '#FFFDE7' },
  ELECTIVE:   { color: '#388E3C', label: 'Elective / Scheduled',   bg: '#E8F5E9' },
};

function ManagementPlan({ verdict }) {
  const steps = verdict?.treatment_approach || [];
  if (!steps.length) return <Typography color="text.secondary">No management plan generated.</Typography>;
  return (
    <Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2, fontStyle: 'italic' }}>
        AI-assisted suggestions — verify with local protocol before implementing.
      </Typography>
      <Stack spacing={1.5}>
        {steps.map((step, i) => (
          <Paper key={i} variant="outlined" sx={{ p: 2 }}>
            <Stack direction="row" spacing={2} alignItems="flex-start">
              <Box sx={{ minWidth: 32, height: 32, borderRadius: '50%', bgcolor: 'primary.main', color: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold', fontSize: 14, flexShrink: 0 }}>
                {step.priority}
              </Box>
              <Box sx={{ flex: 1 }}>
                <Typography variant="body1" fontWeight={600}>{step.action}</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{step.rationale}</Typography>
                {step.guideline_basis && (
                  <Chip label={step.guideline_basis} size="small" variant="outlined" sx={{ mt: 1, fontSize: 11 }} />
                )}
              </Box>
            </Stack>
          </Paper>
        ))}
      </Stack>
    </Box>
  );
}

function ResourceChecklist({ verdict }) {
  const req = verdict?.facility_requirements;
  if (!req) return <Typography color="text.secondary">No resource checklist generated.</Typography>;
  const { equipment = [], drugs = [], personnel = [] } = req;
  const Section = ({ title, items, color }) => (
    <Box>
      <Typography variant="subtitle2" fontWeight={700} color={color} gutterBottom>{title}</Typography>
      {items.length === 0 ? (
        <Typography variant="body2" color="text.secondary">None listed.</Typography>
      ) : (
        <List dense disablePadding>
          {items.map((item, i) => (
            <ListItem key={i} disableGutters sx={{ py: 0.25 }}>
              <ListItemIcon sx={{ minWidth: 28 }}><CheckBoxOutlineBlank fontSize="small" /></ListItemIcon>
              <ListItemText primary={item} primaryTypographyProps={{ variant: 'body2' }} />
            </ListItem>
          ))}
        </List>
      )}
    </Box>
  );
  return (
    <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: 'repeat(3, 1fr)' }, gap: 3 }}>
      <Section title="Equipment" items={equipment} color="primary.main" />
      <Section title="Drugs (Drug Class)" items={drugs} color="error.dark" />
      <Section title="Personnel" items={personnel} color="success.dark" />
    </Box>
  );
}

function ReferralGuide({ verdict }) {
  const urgencyKey = verdict?.referral_urgency;
  const urgencyConfig = URGENCY_CONFIG[urgencyKey] || {};
  const criteria = verdict?.referral_criteria || [];
  return (
    <Box>
      {urgencyKey && (
        <Paper sx={{ p: 2, mb: 3, bgcolor: urgencyConfig.bg, borderLeft: `6px solid ${urgencyConfig.color}` }}>
          <Stack direction="row" spacing={2} alignItems="center">
            <AccessTime sx={{ color: urgencyConfig.color, fontSize: 32 }} />
            <Box>
              <Typography variant="h6" fontWeight={700} sx={{ color: urgencyConfig.color }}>
                {urgencyConfig.label}
              </Typography>
              {verdict?.referral_time_rationale && (
                <Typography variant="body2" sx={{ mt: 0.5 }}>{verdict.referral_time_rationale}</Typography>
              )}
            </Box>
          </Stack>
        </Paper>
      )}
      {criteria.length > 0 && (
        <Box>
          <Typography variant="subtitle1" fontWeight={700} gutterBottom>Referral Trigger Criteria</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            If any of these thresholds are met, referral becomes mandatory.
          </Typography>
          <TableContainer component={Paper} variant="outlined">
            <Table size="small">
              <TableHead>
                <TableRow sx={{ bgcolor: 'primary.main' }}>
                  <TableCell sx={{ color: 'white', fontWeight: 700 }}>Clinical Criterion</TableCell>
                  <TableCell sx={{ color: 'white', fontWeight: 700 }}>Threshold / Trigger</TableCell>
                  <TableCell sx={{ color: 'white', fontWeight: 700 }}>Refer To</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {criteria.map((rc, i) => (
                  <TableRow key={i} sx={{ '&:nth-of-type(odd)': { bgcolor: 'grey.50' } }}>
                    <TableCell>{rc.criterion}</TableCell>
                    <TableCell sx={{ fontWeight: 600, color: 'error.dark' }}>{rc.threshold}</TableCell>
                    <TableCell>{rc.specialty}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Box>
      )}
      {!urgencyKey && criteria.length === 0 && (
        <Typography color="text.secondary">No referral guidance generated.</Typography>
      )}
    </Box>
  );
}

function BridgingCare({ verdict }) {
  const actions = verdict?.bridging_care || [];
  const facilityNote = {
    'Level 1 PHC': 'Transit to specialist may take 12–24 hours. Some steps below are written for the accompanying family member or ASHA worker — not a doctor. Share this with whoever travels with the patient.',
    'District Hospital': 'Transfer to specialist facility typically takes 1–3 hours. Steps below apply from decision-to-refer until the patient is received at the destination.',
    'Tertiary Medical College': 'Covers the gap until the specialist team arrives on ward, typically 30–90 minutes.',
  };
  const note = facilityNote[verdict?.facility_level] || 'Actions to take until specialist review is available.';
  if (!actions.length) return <Typography color="text.secondary">No bridging care plan generated.</Typography>;
  return (
    <Box>
      <Paper sx={{ p: 2, mb: 2, bgcolor: '#FFF8E1', borderLeft: `6px solid ${URGENCY_CONFIG.WITHIN_4HRS.color}` }}>
        <Stack direction="row" spacing={1} alignItems="flex-start">
          <WarningAmber sx={{ color: URGENCY_CONFIG.WITHIN_4HRS.color, mt: 0.25, flexShrink: 0 }} />
          <Typography variant="body2" fontWeight={600}>{note}</Typography>
        </Stack>
      </Paper>
      <Stack spacing={1.5}>
        {actions.map((bc, i) => (
          <Paper key={i} variant="outlined" sx={{ p: 2 }}>
            <Stack direction="row" spacing={2} alignItems="flex-start">
              <MedicalServices sx={{ color: 'warning.dark', mt: 0.25, flexShrink: 0 }} />
              <Box sx={{ flex: 1 }}>
                <Typography variant="body1" fontWeight={600}>{bc.action}</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{bc.rationale}</Typography>
                <Chip label={bc.time_frame} size="small" sx={{ mt: 1, bgcolor: '#FFF3E0', color: '#E65100', fontWeight: 600, fontSize: 11 }} />
              </Box>
            </Stack>
          </Paper>
        ))}
      </Stack>
    </Box>
  );
}

function TabPanel({ children, value, index }) {
  return value === index ? <Box sx={{ pt: 2 }}>{children}</Box> : null;
}

export default function ResultPage() {
  const [tab, setTab] = useState(0);
  const { verdict, sessionId, doctor } = useTriageStore();
  const navigate = useNavigate();

  const [doctorName, setDoctorName] = useState(doctor?.name || '');
  const [impression, setImpression] = useState('');
  const [suggestions, setSuggestions] = useState('');
  const [saving, setSaving] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [snack, setSnack] = useState({ open: false, msg: '', severity: 'success' });

  const showSnack = (msg, severity = 'success') => setSnack({ open: true, msg, severity });

  useEffect(() => {
    if (!sessionId) return;
    getDoctorNotes(sessionId)
      .then((notes) => {
        if (notes && notes.doctor_name !== undefined) {
          setDoctorName(notes.doctor_name || '');
          setImpression(notes.clinical_impression || '');
          setSuggestions(notes.suggestions || '');
        }
      })
      .catch(() => {});
  }, [sessionId]);

  const handleSaveNotes = async () => {
    if (!sessionId) {
      showSnack('No active session — cannot save notes.', 'error');
      return;
    }
    setSaving(true);
    try {
      await saveDoctorNotes(sessionId, {
        doctor_name: doctorName,
        clinical_impression: impression,
        suggestions,
      });
      showSnack('Notes saved successfully.');
    } catch {
      showSnack('Failed to save notes.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleDownload = async () => {
    if (!sessionId) {
      showSnack('No active session — cannot generate PDF.', 'error');
      return;
    }
    setDownloading(true);
    try {
      const blob = await downloadReport(sessionId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `triage_report_${sessionId.slice(0, 8)}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      showSnack('Failed to generate PDF.', 'error');
    } finally {
      setDownloading(false);
    }
  };

  if (!verdict) {
    return (
      <Box sx={{ textAlign: 'center', mt: 8 }}>
        <Typography variant="h6" color="text.secondary">No triage result yet.</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          Start a triage from the intake form to see results here.
        </Typography>
      </Box>
    );
  }

  const urgencyKey = verdict?.referral_urgency;
  const urgencyConfig = URGENCY_CONFIG[urgencyKey];

  return (
    <Box>
      <VerdictHeader verdict={verdict} />

      {urgencyKey === 'IMMEDIATE' && (
        <Paper sx={{ p: 2, mb: 2, bgcolor: '#FFEBEE', border: '2px solid #B71C1C', borderRadius: 2 }}>
          <Stack direction="row" spacing={1} alignItems="center">
            <WarningAmber sx={{ color: '#B71C1C', fontSize: 28 }} />
            <Box>
              <Typography variant="subtitle1" fontWeight={700} color="#B71C1C">
                IMMEDIATE REFERRAL — Activate transfer NOW
              </Typography>
              {verdict?.referral_time_rationale && (
                <Typography variant="body2">{verdict.referral_time_rationale}</Typography>
              )}
            </Box>
          </Stack>
        </Paper>
      )}

      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 1 }}>
        <Button
          variant="contained"
          startIcon={downloading ? <CircularProgress size={16} color="inherit" /> : <Download />}
          onClick={handleDownload}
          disabled={downloading}
          size="small"
        >
          {downloading ? 'Generating PDF...' : 'Download Report PDF'}
        </Button>
      </Box>

      <SafetyAlerts alerts={verdict.safety_alerts} />

      <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="scrollable" scrollButtons="auto" sx={{ borderBottom: 1, borderColor: 'divider' }}>
        <Tab label="Verdict" />
        <Tab label="Management Plan" />
        <Tab label="Referral Guide" />
        <Tab label="Bridging Care" />
        <Tab label="Resource Checklist" />
        <Tab label="Workup Plan" />
        <Tab label="Council Summary" />
        <Tab label="Other Departments" />
      </Tabs>

      <TabPanel value={tab} index={0}>
        <ExplanationCard explanation={verdict.explanation} keyFactors={verdict.key_factors} />
        <DepartmentRouting verdict={verdict} />
      </TabPanel>

      <TabPanel value={tab} index={1}>
        <ManagementPlan verdict={verdict} />
      </TabPanel>

      <TabPanel value={tab} index={2}>
        <ReferralGuide verdict={verdict} />
      </TabPanel>

      <TabPanel value={tab} index={3}>
        <BridgingCare verdict={verdict} />
      </TabPanel>

      <TabPanel value={tab} index={4}>
        <ResourceChecklist verdict={verdict} />
      </TabPanel>

      <TabPanel value={tab} index={5}>
        <WorkupTable workup={verdict.consolidated_workup} />
      </TabPanel>

      <TabPanel value={tab} index={6}>
        <CouncilSummary verdict={verdict} />
      </TabPanel>

      <TabPanel value={tab} index={7}>
        <OtherDepartments departments={verdict.other_departments_flagged} />
      </TabPanel>

      <Divider sx={{ my: 3 }} />

      <Paper variant="outlined" sx={{ p: 3 }}>
        <Typography variant="h6" gutterBottom>Doctor's Notes</Typography>

        <Stack spacing={2}>
          <TextField
            label="Doctor Name"
            value={doctorName}
            onChange={(e) => setDoctorName(e.target.value)}
            size="small"
            fullWidth
          />
          <TextField
            label="Clinical Impression"
            value={impression}
            onChange={(e) => setImpression(e.target.value)}
            multiline
            rows={4}
            fullWidth
            placeholder="Your clinical assessment of this patient..."
          />
          <TextField
            label="Additional Suggestions"
            value={suggestions}
            onChange={(e) => setSuggestions(e.target.value)}
            multiline
            rows={3}
            fullWidth
            placeholder="Any additional recommendations or follow-up actions..."
          />
          <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Button
              variant="contained"
              startIcon={saving ? <CircularProgress size={16} color="inherit" /> : <Save />}
              onClick={handleSaveNotes}
              disabled={saving}
            >
              {saving ? 'Saving...' : 'Save Notes'}
            </Button>
          </Box>
        </Stack>
      </Paper>

      <Snackbar
        open={snack.open}
        autoHideDuration={4000}
        onClose={() => setSnack((s) => ({ ...s, open: false }))}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity={snack.severity} onClose={() => setSnack((s) => ({ ...s, open: false }))}>
          {snack.msg}
        </Alert>
      </Snackbar>
    </Box>
  );
}
