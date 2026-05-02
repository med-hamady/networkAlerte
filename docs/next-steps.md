# Next Steps

Recommended development roadmap after the initial environment setup.

## Phase 1 — Device Discovery & Basic Monitoring

- [ ] Implement ICMP ping polling (check device reachability)
- [ ] Implement SNMP metric collection (signal, noise, uptime, traffic)
- [ ] Auto-update device `status` and `last_seen` on each poll
- [ ] Store collected metrics in `device_metrics`

## Phase 2 — UISP Power Monitoring

- [ ] Integrate with UISP Power API or SNMP
- [ ] Poll voltage, current, power readings
- [ ] Store readings in `power_status_logs`
- [ ] Define thresholds for power anomalies

## Phase 3 — Incident Detection & Alerting

- [ ] Define detection rules (device down, high latency, power loss)
- [ ] Create incidents automatically when anomalies are detected
- [ ] Implement notification channels (email via SMTP, webhooks)
- [ ] Send alerts when incidents are created or escalated

## Phase 4 — SSH Automation

- [ ] Device configuration backup via SSH/paramiko
- [ ] Remote command execution
- [ ] Firmware version tracking

## Phase 5 — Dashboard / Frontend

- [ ] Build a Next.js or React frontend
- [ ] Device list with status indicators
- [ ] Incident timeline
- [ ] Power monitoring graphs
- [ ] Real-time updates via WebSocket

## Phase 6 — Testing & Production Readiness

- [ ] Unit tests for services and API
- [ ] Integration tests with test database
- [ ] CI/CD pipeline
- [ ] Production Docker Compose / deployment config
- [ ] Security hardening (authentication, HTTPS, secrets management)
