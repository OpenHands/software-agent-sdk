function list(items) {
  return items.length ? items.map((item) => `- \`${item}\``).join('\n') : '_none_';
}

function renderExceptionGroups(title, exceptions) {
  if (!exceptions.length) return '';

  const groups = new Map();
  for (const exception of exceptions) {
    const key = JSON.stringify({
      reason: exception.reason,
      owner: exception.owner,
      tracking: exception.tracking,
    });
    const group = groups.get(key) ?? { ...exception, endpoints: [] };
    group.endpoints.push(exception.endpoint);
    groups.set(key, group);
  }

  return [
    `#### ${title} (${exceptions.length})`,
    '',
    ...[...groups.values()].flatMap((group) => [
      list(group.endpoints),
      '',
      `Reason: ${group.reason}`,
      `Owner: ${group.owner}`,
      ...(group.tracking ? [`Tracking: ${group.tracking}`] : []),
      '',
    ]),
  ].join('\n');
}

function renderEndpointAuditReport(report) {
  const clientOnly = report.clientOnly || report.mismatch || [];
  const serverOnly = report.serverOnly || report.missingApi || [];
  const allowedClientOnly = report.allowedClientOnly || [];
  const explicitlyCovered = report.explicitlyCoveredServerOperations || [];
  const actionableCount = clientOnly.length + serverOnly.length;
  const documentedCount = allowedClientOnly.length + explicitlyCovered.length;
  const blocking = Boolean(report.gate?.clientOnly || report.gate?.serverOnly);
  const status = actionableCount
    ? `⚠️ ${actionableCount} actionable Agent Server contract divergence(s)`
    : '✅ No actionable handwritten Agent Server contract divergences';
  const mode = blocking ? 'blocking' : 'report-only';
  const source = report.contractSources?.[0];
  const sourceLine = source
    ? source.startsWith('https://')
      ? `Contract: [pinned release artifact](${source})`
      : `Contract: \`${source}\``
    : undefined;
  const summary = [
    '| Category | Count |',
    '| --- | ---: |',
    `| Actionable client-only calls | ${clientOnly.length} |`,
    `| Actionable server-only operations | ${serverOnly.length} |`,
    `| Documented non-divergences | ${documentedCount} |`,
    `| Agent Server contract operations | ${report.agentServer} |`,
    `| Audited handwritten client endpoints | ${report.client} |`,
  ].join('\n');
  const documentedDetails = [
    '<details>',
    `<summary>Documented non-divergences (${documentedCount})</summary>`,
    '',
    renderExceptionGroups(
      'Client calls intentionally absent from the filtered contract',
      allowedClientOnly
    ),
    renderExceptionGroups('Server operations covered by an exposed browser URL', explicitlyCovered),
    '</details>',
  ].join('\n');

  return [
    '## Endpoint audit',
    '',
    `**${status}** · ${mode}`,
    ...(sourceLine ? ['', sourceLine] : []),
    '',
    summary,
    '',
    `### Actionable client-only calls (${clientOnly.length})`,
    '',
    list(clientOnly),
    '',
    `### Actionable server-only operations (${serverOnly.length})`,
    '',
    list(serverOnly),
    ...(documentedCount ? ['', documentedDetails] : []),
  ].join('\n');
}

module.exports = { renderEndpointAuditReport };
