import { useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  Box, Drawer, List, ListItemButton, ListItemIcon, ListItemText,
  AppBar, Toolbar, Typography, IconButton, Tooltip, Button,
} from '@mui/material';
import {
  LocalHospital, People, Groups, BarChart, Bolt, Menu as MenuIcon, Logout, Info,
} from '@mui/icons-material';
import useTriageStore from '../state/triageStore';
import logo from '../assets/logo.png';

const DRAWER_W = 220;
const COLLAPSED_W = 60;

const NAV = [
  { label: 'New Triage',    icon: <LocalHospital />, path: '/' },
  { label: 'Quick Triage',  icon: <Bolt />,          path: '/quick-triage' },
  { label: 'Patient Queue', icon: <People />,         path: '/queue' },
  { label: 'Council View',  icon: <Groups />,         path: '/council' },
  { label: 'Analytics',     icon: <BarChart />,       path: '/analytics' },
  { label: 'About Ydhya',   icon: <Info />,           path: '/about' },
];

function SidebarContent({ expanded, onNavigate, pathname }) {
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Brand */}
      <Box sx={{
        display: 'flex', alignItems: 'center', gap: 1.5,
        px: expanded ? 2 : 1, py: 2,
        minHeight: 64,
        borderBottom: '1px solid #E0E0E0',
        overflow: 'hidden',
        whiteSpace: 'nowrap',
      }}>
        {expanded ? (
          <img src={logo} alt="Ydhya" style={{ height: 36, objectFit: 'contain' }} />
        ) : (
          <img src={logo} alt="Ydhya" style={{ height: 28, width: 28, objectFit: 'contain', objectPosition: 'left' }} />
        )}
      </Box>

      {/* Nav */}
      <List sx={{ pt: 1, px: 0.5 }}>
        {NAV.map((item) => (
          <Tooltip
            key={item.path}
            title={expanded ? '' : item.label}
            placement="right"
            arrow
          >
            <ListItemButton
              selected={pathname === item.path}
              onClick={() => onNavigate(item.path)}
              sx={{
                borderRadius: 2, mb: 0.5,
                justifyContent: expanded ? 'flex-start' : 'center',
                px: expanded ? 2 : 1,
                minHeight: 44,
              }}
            >
              <ListItemIcon sx={{ minWidth: expanded ? 36 : 'unset', color: 'inherit' }}>
                {item.icon}
              </ListItemIcon>
              {expanded && <ListItemText primary={item.label} primaryTypographyProps={{ fontSize: 14 }} />}
            </ListItemButton>
          </Tooltip>
        ))}
      </List>
    </Box>
  );
}

export default function DashboardLayout() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [hovered, setHovered] = useState(false);
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { doctor, logout } = useTriageStore();

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const handleNavigate = (path) => {
    navigate(path);
    setMobileOpen(false);
  };

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh' }}>
      {/* Mobile drawer */}
      <Drawer
        variant="temporary"
        open={mobileOpen}
        onClose={() => setMobileOpen(false)}
        sx={{ display: { xs: 'block', md: 'none' }, '& .MuiDrawer-paper': { width: DRAWER_W } }}
      >
        <SidebarContent expanded pathname={pathname} onNavigate={handleNavigate} />
      </Drawer>

      {/* Desktop hover-expand drawer */}
      <Drawer
        variant="permanent"
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        sx={{
          display: { xs: 'none', md: 'block' },
          '& .MuiDrawer-paper': {
            width: hovered ? DRAWER_W : COLLAPSED_W,
            transition: 'width 0.2s ease',
            overflowX: 'hidden',
            boxShadow: hovered ? '2px 0 12px rgba(0,0,0,0.1)' : 'none',
          },
        }}
      >
        <SidebarContent expanded={hovered} pathname={pathname} onNavigate={handleNavigate} />
      </Drawer>

      {/* Main content */}
      <Box sx={{
        flexGrow: 1,
        ml: { xs: 0, md: `${COLLAPSED_W}px` },
        transition: 'margin 0.2s ease',
        minWidth: 0,
      }}>
        <AppBar position="sticky" elevation={0} sx={{ bgcolor: 'white', color: 'text.primary', borderBottom: '1px solid #E0E0E0' }}>
          <Toolbar variant="dense" sx={{ minHeight: 52 }}>
            <IconButton sx={{ display: { md: 'none' }, mr: 1 }} onClick={() => setMobileOpen(true)}>
              <MenuIcon />
            </IconButton>
            <Typography variant="subtitle1" fontWeight={600}>
              {NAV.find((n) => n.path === pathname)?.label ?? 'Ydhya'}
            </Typography>
            <Box sx={{ flexGrow: 1 }} />
            <Typography variant="body2" color="text.secondary" sx={{ mr: 1 }}>
              {new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </Typography>
            {doctor && (
              <>
                <Typography variant="body2" fontWeight={600} sx={{ mr: 1 }}>
                  Dr. {doctor.name}
                </Typography>
                <Tooltip title="Logout">
                  <IconButton size="small" onClick={handleLogout}>
                    <Logout fontSize="small" />
                  </IconButton>
                </Tooltip>
              </>
            )}
          </Toolbar>
        </AppBar>

        <Box sx={{ p: { xs: 2, md: 3 } }}>
          <Outlet />
        </Box>
      </Box>
    </Box>
  );
}
