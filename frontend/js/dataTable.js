/**
 * Data Table Renderer
 * Professional table rendering for structured database results
 */

/**
 * Render structured data as a professional HTML table
 * @param {HTMLElement} container - Container element for the table
 * @param {Object} data - Structured data from backend
 */
function renderDataTable(container, data) {
    if (!container || !data) return;

    console.log('Rendering data table:', data);

    // Clear container
    container.innerHTML = '';

    // Determine table type and prepare data
    let tableConfig = null;

    if (data.surveys_by_block) {
        tableConfig = prepareSurveyTable(data);
    } else if (data.applications) {
        tableConfig = prepareApplicationsTable(data);
    } else if (data.field_visits) {
        tableConfig = prepareFieldVisitsTable(data);
    } else if (data.owners) {
        tableConfig = prepareOwnersTable(data);
    } else if (data.workload) {
        tableConfig = prepareWorkloadTable(data);
    } else if (data.application_number) {
        tableConfig = prepareApplicationDetailTable(data);
    }

    if (!tableConfig) {
        console.warn('Unknown data structure, cannot render table');
        return;
    }

    // Create table HTML - NOW RETURNS OBJECT
    const tableResult = createTableHTML(tableConfig);
    container.innerHTML = tableResult.html;
    
    // Store full dataset on container for pagination
    const tableCard = container.querySelector('.data-table-card');
    if (tableCard && tableResult.fullDataset) {
        tableCard._fullDataset = tableResult.fullDataset;
        tableCard._currentPage = 1;
        tableCard._tableConfig = tableResult.config;
    }

    // Add event listeners
    setupTableEventListeners(container);
}

/**
 * Prepare survey numbers table configuration
 */
function prepareSurveyTable(data) {
    const rows = [];

    for (const [blockName, surveys] of Object.entries(data.surveys_by_block || {})) {
        for (const survey of surveys) {
            rows.push({
                'Survey Number': survey.survey_no || 'N/A',
                'Area (sqm)': survey.area_sqm ? survey.area_sqm.toLocaleString() : 'N/A',
                'Land Type': survey.land_type || 'N/A',
                'Patta Number': survey.patta_number || 'N/A',
                'Sub-Divisions': survey.subdivisions && survey.subdivisions.length > 0
                    ? survey.subdivisions.join(', ')
                    : 'None'
            });
        }
    }

    return {
        title: data.query_type || 'Survey Numbers',
        columns: ['Survey Number', 'Area (sqm)', 'Land Type', 'Patta Number', 'Sub-Divisions'],
        rows: rows,
        icon: '📋'
    };
}

/**
 * Prepare applications table configuration
 */
function prepareApplicationsTable(data) {
    const rows = data.applications.map(app => {
        const jur = app.jurisdiction || {};
        return {
            'Number': app.application_number || 'N/A',
            'Type': app.type || 'N/A',
            'District': jur.district || app.district_name || 'N/A',
            'Taluk': jur.taluk || app.taluk_name || 'N/A',
            'Town': jur.town || app.town_name || 'N/A',
            'Ward': jur.ward || app.ward_number || 'N/A',
            'Block': jur.block || app.block_number || 'N/A',
            'Status': app.status || app.current_status || 'N/A',
            'Stage': app.stage || app.current_stage || 'N/A',
            'Date': app.submission_date
                ? new Date(app.submission_date).toLocaleDateString()
                : 'N/A'
        };
    });

    return {
        title: data.query_type || 'Applications',
        columns: ['Number', 'Type', 'District', 'Taluk', 'Town', 'Ward', 'Block', 'Status', 'Stage', 'Date'],
        rows: rows,
        icon: '📄'
    };
}

/**
 * Prepare field visits table configuration
 */
function prepareFieldVisitsTable(data) {
    const rows = data.field_visits.map(visit => ({
        'Application Number': visit.application_number || 'N/A',
        'Survey Number': visit.survey_no || 'N/A',
        'Type': visit.application_type || 'N/A',
        'Status': visit.status || 'N/A',
        'Scheduled Date': visit.field_visit_date ? new Date(visit.field_visit_date).toLocaleDateString() : 'Not Scheduled'
    }));

    return {
        title: data.query_type || 'Field Visits',
        columns: ['Application Number', 'Survey Number', 'Type', 'Status', 'Scheduled Date'],
        rows: rows,
        icon: '🗓️'
    };
}

/**
 * Prepare owners table configuration
 */
function prepareOwnersTable(data) {
    const rows = data.owners.map(owner => ({
        'Owner Name': owner.name || owner.owner_name || 'N/A',
        'Sub-Division': owner.sub_division || 'Survey Level',
        'Ownership Share': owner.ownership_share || 'N/A',
        'Ownership Type': owner.ownership_type || 'N/A',
        'Joint Owner': owner.is_joint_owner ? 'Yes' : 'No'
    }));

    return {
        title: data.query_type || `Survey ${data.survey_no || ''} Owners`,
        columns: ['Owner Name', 'Sub-Division', 'Ownership Share', 'Ownership Type', 'Joint Owner'],
        rows: rows,
        icon: '👤'
    };
}

/**
 * Prepare workload table configuration
 */
function prepareWorkloadTable(data) {
    const rows = [{
        'Metric': 'Total Applications',
        'Count': data.workload.total_applications || 0
    }, {
        'Metric': 'Pending Applications',
        'Count': data.workload.pending_applications || 0
    }, {
        'Metric': 'Completed Applications',
        'Count': data.workload.completed_applications || 0
    }];

    return {
        title: data.query_type || 'Workload Summary',
        columns: ['Metric', 'Count'],
        rows: rows,
        icon: '📊'
    };
}

/**
 * Prepare single application and applicant details table configuration
 */
function prepareApplicationDetailTable(data) {
    const rows = [
        { 'Field': 'Application Number', 'Details': data.application_number || 'N/A' },
        { 'Field': 'Application Type', 'Details': data.type || 'N/A' },
        { 'Field': 'Included Sub-divisions', 'Details': data.included_subdivisions || 'None' },
        { 'Field': 'Current Status', 'Details': data.status || 'N/A' },
        { 'Field': 'Current Stage', 'Details': data.stage || 'N/A' },
        { 'Field': 'Submission Date', 'Details': data.submission_date ? new Date(data.submission_date).toLocaleDateString() : 'N/A' },
        { 'Field': 'Field Visit Scheduled', 'Details': data.field_visit_scheduled ? `Yes (${data.field_visit_date ? new Date(data.field_visit_date).toLocaleDateString() : 'N/A'})` : 'No' },
        { 'Field': 'Overdue', 'Details': data.is_overdue ? 'Yes' : 'No' },
        { 'Field': 'Priority Flag', 'Details': data.priority_flag ? 'Yes' : 'No' },
        { 'Field': 'Applicant Name', 'Details': data.applicant_name || 'N/A' },
        { 'Field': 'Applicant Mobile', 'Details': data.applicant_mobile || 'N/A' },
        { 'Field': 'Applicant Email', 'Details': data.applicant_email || 'N/A' },
        { 'Field': 'Applicant Address', 'Details': data.applicant_address || 'N/A' },
        { 'Field': 'Aadhaar (Last 4)', 'Details': data.applicant_aadhaar_last4 || 'N/A' }
    ];

    return {
        title: data.query_type || 'Application & Applicant Details',
        columns: ['Field', 'Details'],
        rows: rows,
        icon: '📋',
        disablePagination: true
    };
}

/**
 * Create HTML for the table
 */
function createTableHTML(config) {
    const { title, columns, rows, icon, disablePagination } = config;

    if (!rows || rows.length === 0) {
        return {
            html: `
            <div class="data-table-card">
                <div class="data-table-header">
                    <span class="data-table-icon">${icon}</span>
                    <h3 class="data-table-title">${escapeHtml(title)}</h3>
                </div>
                <div class="data-table-empty">
                    <p>No data found</p>
                </div>
            </div>
        `,
            fullDataset: []
        };
    }

    // Calculate if we need pagination (more than 10 rows)
    const needsPagination = !disablePagination && rows.length > 10;
    const displayRows = needsPagination ? rows.slice(0, 10) : rows;
    const fullDataset = rows; // Store full dataset for pagination

    let tableHTML = `
        <div class="data-table-card">
            <div class="data-table-header">
                <div class="data-table-title-section">
                    <span class="data-table-icon">${icon}</span>
                    <h3 class="data-table-title">${escapeHtml(title)}</h3>
                    ${disablePagination ? '' : `<span class="data-table-count">${rows.length} record${rows.length !== 1 ? 's' : ''}</span>`}
                </div>
                <button class="btn-copy-table" onclick="copyTableData(this)" title="Copy table data">
                    <i data-lucide="copy" style="width: 16px; height: 16px;"></i>
                </button>
            </div>
            <div class="data-table-container">
                <table class="data-table">
                    <thead>
                        <tr>
    `;

    // Add column headers
    columns.forEach(col => {
        tableHTML += `<th>${escapeHtml(col)}</th>`;
    });

    tableHTML += `
                        </tr>
                    </thead>
                    <tbody>
    `;

    // Add data rows
    displayRows.forEach((row, index) => {
        tableHTML += '<tr>';
        columns.forEach(col => {
            const value = row[col] !== undefined && row[col] !== null ? row[col] : 'N/A';
            tableHTML += `<td>${escapeHtml(String(value))}</td>`;
        });
        tableHTML += '</tr>';
    });

    tableHTML += `
                    </tbody>
                </table>
            </div>
    `;

    // Add pagination controls if needed
    if (needsPagination) {
        tableHTML += `
            <div class="data-table-footer">
                <div class="data-table-pagination">
                    <span class="pagination-info">Showing 1-10 of ${rows.length}</span>
                    <div class="pagination-controls">
                        <button class="btn-pagination" data-page="prev" disabled>
                            <i data-lucide="chevron-left" style="width: 16px; height: 16px;"></i>
                        </button>
                        <span class="pagination-page">Page 1 of ${Math.ceil(rows.length / 10)}</span>
                        <button class="btn-pagination" data-page="next" ${rows.length <= 10 ? 'disabled' : ''}>
                            <i data-lucide="chevron-right" style="width: 16px; height: 16px;"></i>
                        </button>
                    </div>
                </div>
            </div>
        `;
    }

    tableHTML += '</div>';

    return { html: tableHTML, fullDataset: fullDataset, config: config };
}

/**
 * Setup event listeners for table interactions
 */
function setupTableEventListeners(container) {
    // Reinitialize Lucide icons
    if (typeof lucide !== 'undefined') {
        lucide.createIcons();
    }

    // Setup pagination if exists
    const paginationBtns = container.querySelectorAll('.btn-pagination');
    paginationBtns.forEach(btn => {
        btn.addEventListener('click', handlePagination);
    });
}

/**
 * Handle table pagination
 */
function handlePagination(event) {
    const button = event.currentTarget;
    const direction = button.getAttribute('data-page');
    const tableCard = button.closest('.data-table-card');
    
    if (!tableCard || !tableCard._fullDataset || !tableCard._tableConfig) {
        console.warn('Pagination data not available');
        return;
    }
    
    const fullData = tableCard._fullDataset;
    const config = tableCard._tableConfig;
    let currentPage = tableCard._currentPage || 1;
    const rowsPerPage = 10;
    const totalPages = Math.ceil(fullData.length / rowsPerPage);
    
    // Calculate new page
    if (direction === 'next' && currentPage < totalPages) {
        currentPage++;
    } else if (direction === 'prev' && currentPage > 1) {
        currentPage--;
    } else {
        return; // No change needed
    }
    
    // Calculate row range
    const startIdx = (currentPage - 1) * rowsPerPage;
    const endIdx = Math.min(startIdx + rowsPerPage, fullData.length);
    const pageRows = fullData.slice(startIdx, endIdx);
    
    // Re-render table body
    const tbody = tableCard.querySelector('tbody');
    if (!tbody) return;
    
    tbody.innerHTML = '';
    pageRows.forEach(row => {
        const tr = document.createElement('tr');
        config.columns.forEach(col => {
            const td = document.createElement('td');
            const value = row[col] !== undefined && row[col] !== null ? row[col] : 'N/A';
            td.textContent = value;
            tr.appendChild(td);
        });
        tbody.appendChild(tr);
    });
    
    // Update pagination info
    const paginationInfo = tableCard.querySelector('.pagination-info');
    if (paginationInfo) {
        paginationInfo.textContent = `Showing ${startIdx + 1}-${endIdx} of ${fullData.length}`;
    }
    
    const paginationPage = tableCard.querySelector('.pagination-page');
    if (paginationPage) {
        paginationPage.textContent = `Page ${currentPage} of ${totalPages}`;
    }
    
    // Update button states
    const prevBtn = tableCard.querySelector('[data-page="prev"]');
    const nextBtn = tableCard.querySelector('[data-page="next"]');
    
    if (prevBtn) prevBtn.disabled = (currentPage === 1);
    if (nextBtn) nextBtn.disabled = (currentPage === totalPages);
    
    // Store current page
    tableCard._currentPage = currentPage;
}

/**
 * Copy table data to clipboard
 */
window.copyTableData = function (button) {
    const tableCard = button.closest('.data-table-card');
    const table = tableCard.querySelector('.data-table');

    if (!table) return;

    // Extract table data as TSV (tab-separated values)
    let tsvData = '';

    // Headers
    const headers = Array.from(table.querySelectorAll('thead th')).map(th => th.textContent.trim());
    tsvData += headers.join('\t') + '\n';

    // Rows
    const rows = table.querySelectorAll('tbody tr');
    rows.forEach(row => {
        const cells = Array.from(row.querySelectorAll('td')).map(td => td.textContent.trim());
        tsvData += cells.join('\t') + '\n';
    });

    // Copy to clipboard
    navigator.clipboard.writeText(tsvData).then(() => {
        // Show success feedback
        if (typeof showToast === 'function') {
            showToast('Table data copied to clipboard!', 'success');
        }

        // Change icon temporarily
        const icon = button.querySelector('[data-lucide]');
        if (icon) {
            icon.setAttribute('data-lucide', 'check');
            if (typeof lucide !== 'undefined') {
                lucide.createIcons();
            }

            setTimeout(() => {
                icon.setAttribute('data-lucide', 'copy');
                if (typeof lucide !== 'undefined') {
                    lucide.createIcons();
                }
            }, 2000);
        }
    }).catch(err => {
        console.error('Failed to copy table data:', err);
        if (typeof showToast === 'function') {
            showToast('Failed to copy table data', 'error');
        }
    });
};

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
