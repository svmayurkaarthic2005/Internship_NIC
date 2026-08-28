/**
 * SIS Copilot Data Table Renderer
 * Professional, paginated HTML table renderer for structured data
 */

// Pagination state map
const tableStates = new Map();

/**
 * 38 Official Tamil Nadu District Codes & Names Mapping
 */
const DISTRICT_CODES = {
    "01": "Tiruvallur",
    "02": "Chennai",
    "03": "Kancheepuram",
    "04": "Vellore",
    "05": "Dharmapuri",
    "06": "Tiruvannamalai",
    "07": "Viluppuram",
    "08": "Salem",
    "09": "Namakkal",
    "10": "Erode",
    "11": "Nilgiris",
    "12": "Coimbatore",
    "13": "Dindigul",
    "14": "Karur",
    "15": "Tiruchirappalli",
    "16": "Perambalur",
    "17": "Ariyalur",
    "18": "Cuddalore",
    "19": "Nagapattinam",
    "20": "Tiruvarur",
    "21": "Thanjavur",
    "22": "Pudukkottai",
    "23": "Sivagangai",
    "24": "Madurai",
    "25": "Theni",
    "26": "Virudhunagar",
    "27": "Ramanathapuram",
    "28": "Thoothukudi",
    "29": "Tirunelveli",
    "30": "Kanniyakumari",
    "31": "Krishnagiri",
    "32": "Tiruppur",
    "33": "Kallakurichi",
    "34": "Chengalpattu",
    "35": "Ranipet",
    "36": "Tirupathur",
    "37": "Tenkasi",
    "38": "Mayiladuthurai"
};

function getDistrictName(codeOrName) {
    if (!codeOrName) return 'N/A';
    const clean = String(codeOrName).trim();
    const formattedCode = clean.padStart(2, '0');
    if (DISTRICT_CODES[formattedCode]) {
        return DISTRICT_CODES[formattedCode];
    }
    if (DISTRICT_CODES[clean]) {
        return DISTRICT_CODES[clean];
    }
    return clean;
}


/**
 * Tamil column header translations.
 * Used when data.language === 'ta'.
 */
const TAMIL_LABELS = {
    // Applications table
    'Application Number': 'விண்ணப்ப எண்',
    'Type':               'வகை',
    'Town':               'நகரம்',
    'Ward':               'வார்டு',
    'Status':             'நிலை',
    'Stage':              'கட்டம்',
    'Submitted Date':     'சமர்ப்பித்த தேதி',
    // Field visits
    'Survey Number':      'கணக்கெண்',
    'Block':              'தொகுதி',
    'Scheduled Date':     'திட்டமிடப்பட்ட தேதி',
    // Survey table
    'Survey No.':         'கணக்கெண்',
    'Area (sqm)':         'பரப்பளவு (ச.மீ)',
    'Land Type':          'நில வகை',
    'Sub-Divisions':      'உட்பிரிவுகள்',
    'Location Chain':     'இடம்',
    // Owners table
    'Owner Name':         'உரிமையாளர் பெயர்',
    'Sub-Division':       'உட்பிரிவு',
    'Ownership Share':    'உரிமை பங்கு',
    'Ownership Type':     'உரிமை வகை',
    'Joint Owner':        'கூட்டு உரிமையாளர்',
    // Workload
    'Metric':             'அளவீடு',
    'Count':              'எண்ணிக்கை',
    // Application detail
    'Field':              'புலம்',
    'Details':            'விவரங்கள்',
    // Workflow
    'Step':               'படி',
    'From Stage':         'இருந்து கட்டம்',
    'To Stage':           'கட்டம் வரை',
    'Date':               'தேதி',
    'Changed By':         'மாற்றியவர்',
    'Note':               'குறிப்பு',
    // Rejection
    'Rejected By':        'நிராகரித்தவர்',
    'Reason':             'காரணம்',
    'Rejected On':        'நிராகரிக்கப்பட்ட தேதி',
    'Resubmitted':        'மறு சமர்ப்பிப்பு',
    // Jurisdiction
    'Level':              'நிலை',
    'Code / Number':      'குறியீடு / எண்',
    'Name':               'பெயர்',
    // FV detail table
    'App No':             'விண்ணப்ப எண்',
    'Applicant':          'விண்ணப்பதாரர்',
    'Survey No':          'கணக்கெண்',
    'Temp Sub Div (SIS)': 'தற்காலிக உட்பிரிவு (SIS)',
    'Fixed Sub Div (DIS)':'நிரந்தர உட்பிரிவு (DIS)',
    'Stage':              'கட்டம்',
    'Days Pending':       'நிலுவையில் உள்ள நாட்கள்',
    'Priority':           'முன்னுரிமை',
    'Number':             'விண்ணப்ப எண்',
};

/**
 * Translate a column header using TAMIL_LABELS if language is Tamil.
 */
function _th(label, isTamil) {
    if (!isTamil) return label;
    return TAMIL_LABELS[label] || label;
}

/**
 * Translate column array
 */
function _translateCols(cols, isTamil) {
    if (!isTamil) return cols;
    return cols.map(c => _th(c, true));
}

/**
 * Translate row keys from English to Tamil column names.
 * Since rows use English keys matching the English column names,
 * we rebuild the row with translated keys when isTamil is true.
 */
function _translateRow(row, engCols, tamCols) {
    if (engCols === tamCols) return row; // no-op for English
    const newRow = {};
    engCols.forEach((eng, i) => {
        newRow[tamCols[i]] = row[eng];
    });
    return newRow;
}

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

    // Always use English headers regardless of language so that
    // status-badge logic (which keys on 'Status' / 'Stage') works correctly.
    const isTamil = false;

    // ── Multi-app query: render each application detail table separately ──
    if (data.multi_tables && Array.isArray(data.multi_tables)) {
        data.multi_tables.forEach((tableData, idx) => {
            const wrapper = document.createElement('div');
            wrapper.className = 'multi-app-table-wrapper';
            if (idx > 0) wrapper.style.marginTop = '16px';
            container.appendChild(wrapper);
            renderDataTable(wrapper, tableData);
        });
        return;
    }

    // Determine table type and prepare data
    let tableConfig = null;

    if (data.surveys_by_block) {
        tableConfig = prepareSurveyTable(data, isTamil);
    } else if (data.applications) {
        tableConfig = prepareApplicationsTable(data, isTamil);
    } else if (data.field_visits) {
        tableConfig = prepareFieldVisitsTable(data, isTamil);
    } else if (data.owners) {
        tableConfig = prepareOwnersTable(data, isTamil);
    } else if (data.workload) {
        tableConfig = prepareWorkloadTable(data, isTamil);
    } else if (data.jurisdiction) {
        tableConfig = prepareJurisdictionTable(data, isTamil);
    } else if (data.rejections) {
        tableConfig = prepareRejectionTable(data, isTamil);
    } else if (data.history) {
        tableConfig = prepareWorkflowTable(data, isTamil);
    } else if (data.application_number) {
        tableConfig = prepareApplicationDetailTable(data, isTamil);
    }

    if (!tableConfig) {
        console.warn('Unknown data structure or empty results, cannot render table');
        container.innerHTML = '<div class="data-table-empty">No records found.</div>';
        return;
    }

    // Create table HTML
    const tableHTML = createTableHTML(tableConfig);
    container.innerHTML = tableHTML;

    // Add event listeners
    setupTableEventListeners(container);
}

/**
 * Prepare survey numbers table configuration
 */
function prepareSurveyTable(data, isTamil = false) {
    const rows = [];
    const hasJurisdiction = !!data.jurisdiction;

    for (const [blockNumber, surveys] of Object.entries(data.surveys_by_block || {})) {
        for (const survey of surveys) {
            const rowData = {
                'Survey Number': survey.survey_no || 'N/A',
                'Area (sqm)': survey.area_sqm ? Number(survey.area_sqm).toLocaleString() : 'N/A',
                'Land Type': survey.land_type || 'N/A',
                'Sub-Divisions': survey.subdivisions && survey.subdivisions.length > 0
                    ? survey.subdivisions.join(', ')
                    : 'None'
            };

            if (hasJurisdiction) {
                const wardName = data.jurisdiction.ward || data.jurisdiction.ward_number || 'N/A';
                const townName = data.jurisdiction.town || 'N/A';
                rowData['Location Chain'] = `Town ${townName} → Ward ${wardName} → Block ${blockNumber}`;
            }

            rows.push(rowData);
        }
    }

    const engCols = ['Survey Number', 'Area (sqm)', 'Land Type', 'Sub-Divisions'];
    if (hasJurisdiction) engCols.push('Location Chain');
    const tamCols = _translateCols(engCols, isTamil);
    const transRows = rows.map(r => _translateRow(r, engCols, tamCols));

    return {
        title: data.query_type || 'Survey Number Details',
        columns: tamCols,
        rows: transRows,
        icon: '📋'
    };
}

/**
 * Prepare applications table configuration
 */
function prepareApplicationsTable(data, isTamil = false) {
    const apps = data.applications || [];

    // FV detail style (from fv_unassigned_awaiting / immediate_action)
    const isFvDetail = apps.length > 0 && ('applicant_name' in apps[0] || 'days_pending' in apps[0]);

    if (isFvDetail) {
        // === Single record: show as full detail card ===
        if (apps.length === 1) {
            const app = apps[0];
            const engCols = ['Field', 'Value'];
            const tamCols = _translateCols(engCols, isTamil);
            const fld = (k) => _th(k, isTamil);
            const rows = [
                { [tamCols[0]]: fld('App No'),                  [tamCols[1]]: app.application_number  || 'N/A' },
                { [tamCols[0]]: fld('Applicant'),               [tamCols[1]]: app.applicant_name      || 'N/A' },
                { [tamCols[0]]: fld('Survey No'),               [tamCols[1]]: app.survey_no           || 'N/A' },
                { [tamCols[0]]: fld('Temp Sub Div (SIS)'),      [tamCols[1]]: app.sis_temp_sub_div    || 'N/A' },
                { [tamCols[0]]: fld('Fixed Sub Div (DIS)'),     [tamCols[1]]: app.dis_fixed_sub_div   || 'N/A' },
                { [tamCols[0]]: fld('Town'),                    [tamCols[1]]: app.town_name           || 'N/A' },
                { [tamCols[0]]: fld('Ward'),                    [tamCols[1]]: app.ward_number  ? `Ward ${app.ward_number}`   : 'N/A' },
                { [tamCols[0]]: fld('Block'),                   [tamCols[1]]: app.block_number ? `Block ${app.block_number}` : 'N/A' },
                { [tamCols[0]]: fld('Stage'),                   [tamCols[1]]: app.current_stage   || 'N/A' },
                { [tamCols[0]]: fld('Status'),                  [tamCols[1]]: app.current_status  || 'N/A' },
                { [tamCols[0]]: fld('Submitted Date'),          [tamCols[1]]: app.submission_date ? new Date(app.submission_date).toLocaleDateString() : 'N/A' },
                { [tamCols[0]]: fld('Days Pending'),            [tamCols[1]]: app.days_pending ?? 'N/A' },
                { [tamCols[0]]: fld('Priority'),                [tamCols[1]]: app.priority || 'Normal' },
            ];
            return {
                title: data.query_type || 'Application Details',
                columns: tamCols,
                rows: rows,
                icon: '📋',
                disablePagination: true
            };
        }

        // === Multiple records: show as wide table ===
        const engCols = ['App No', 'Applicant', 'Survey No', 'Temp Sub Div (SIS)', 'Fixed Sub Div (DIS)',
                         'Town', 'Ward', 'Block', 'Stage', 'Status', 'Days Pending', 'Priority'];
        const tamCols = _translateCols(engCols, isTamil);
        const rows = apps.map(app => {
            const r = {
                'App No':                app.application_number || 'N/A',
                'Applicant':             app.applicant_name     || 'N/A',
                'Survey No':             app.survey_no          || 'N/A',
                'Temp Sub Div (SIS)':    app.sis_temp_sub_div   || 'N/A',
                'Fixed Sub Div (DIS)':   app.dis_fixed_sub_div  || 'N/A',
                'Town':                  app.town_name          || 'N/A',
                'Ward':                  app.ward_number  ? `Ward ${app.ward_number}`   : 'N/A',
                'Block':                 app.block_number ? `Block ${app.block_number}` : 'N/A',
                'Stage':                 app.current_stage  || 'N/A',
                'Status':                app.current_status || 'N/A',
                'Days Pending':          app.days_pending ?? 'N/A',
                'Priority':              app.priority || 'Normal'
            };
            return _translateRow(r, engCols, tamCols);
        });
        return {
            title: data.query_type || 'Applications',
            columns: tamCols,
            rows: rows,
            icon: '🗓️'
        };
    }

    // Standard style
    const engCols = ['Application Number', 'Type', 'Survey Number', 'Sub-Divisions', 'District', 'Taluk', 'Town', 'Ward', 'Block', 'Status', 'Stage', 'Submitted Date'];
    const tamCols = _translateCols(engCols, isTamil);
    const rows = apps.map(app => {
        const jur = app.jurisdiction || {};
        const getVal = (primary, secondary) => (primary && primary !== 'N/A') ? primary : ((secondary && secondary !== 'N/A') ? secondary : 'N/A');

        const rawBlock = (jur.block && jur.block !== 'N/A') ? jur.block : (app.block_number || app.block);
        const blockVal = rawBlock && rawBlock !== 'N/A' 
            ? (String(rawBlock).toLowerCase().includes('block') ? String(rawBlock) : `Block ${rawBlock}`)
            : 'N/A';

        const rawWard = (jur.ward && jur.ward !== 'N/A') ? jur.ward : (app.ward_number || app.ward);
        const wardVal = rawWard && rawWard !== 'N/A'
            ? (String(rawWard).toLowerCase().includes('ward') ? String(rawWard) : `Ward ${rawWard}`)
            : 'N/A';

        const r = {
            'Application Number': app.application_number || 'N/A',
            'Type':               app.type || 'N/A',
            'Survey Number':      app.raw_survey_no || app.survey_no || 'N/A',
            'Sub-Divisions':      app.subdivisions || app.sub_division_no || app.included_subdivisions || '-',
            'District':           getVal(jur.district, app.district_name),
            'Taluk':              getVal(jur.taluk, app.taluk_name),
            'Town':               getVal(jur.town, app.town_name),
            'Ward':               wardVal,
            'Block':              blockVal,
            'Status':             app.status || 'Pending',
            'Stage':              app.current_stage || app.stage || 'N/A',
            'Submitted Date':     app.submission_date ? new Date(app.submission_date).toLocaleDateString() : 'N/A'
        };
        return _translateRow(r, engCols, tamCols);
    });

    return {
        title: data.query_type || 'Pending Applications',
        columns: tamCols,
        rows: rows,
        icon: '📄'
    };
}

/**
 * Prepare field visits table configuration
 */
function prepareFieldVisitsTable(data, isTamil = false) {
    const engCols = ['Application Number', 'Survey Number', 'Sub-Divisions', 'Block', 'Type', 'Status', 'Scheduled Date'];
    const tamCols = _translateCols(engCols, isTamil);
    const rows = data.field_visits.map(visit => {
        const r = {
            'Application Number': visit.application_number || 'N/A',
            'Survey Number':      visit.raw_survey_no || visit.survey_no || 'N/A',
            'Sub-Divisions':      visit.subdivisions || visit.sub_division_no || '-',
            'Block':              visit.block_number ? `Block ${visit.block_number}` : 'N/A',
            'Type':               visit.application_type || visit.type || 'N/A',
            'Status':             visit.status || 'N/A',
            'Scheduled Date':     (visit.field_visit_date || visit.scheduled_date) ? (new Date(visit.field_visit_date || visit.scheduled_date).toLocaleDateString() !== 'Invalid Date' ? new Date(visit.field_visit_date || visit.scheduled_date).toLocaleDateString() : (visit.field_visit_date || visit.scheduled_date)) : 'Not Scheduled'
        };
        return _translateRow(r, engCols, tamCols);
    });

    return {
        title: data.query_type || 'Field Visits',
        columns: tamCols,
        rows: rows,
        icon: '🗓️'
    };
}

/**
 * Prepare owners table configuration
 */
function prepareOwnersTable(data, isTamil = false) {
    const engCols = ['Owner Name', 'Sub-Division', 'Ownership Share', 'Ownership Type', 'Joint Owner'];
    const tamCols = _translateCols(engCols, isTamil);
    const rows = data.owners.map(owner => {
        const r = {
            'Owner Name':      owner.owner_name || owner.name || 'N/A',
            'Sub-Division':    owner.sub_division || 'Survey Level',
            'Ownership Share': owner.ownership_share || 'N/A',
            'Ownership Type':  owner.ownership_type || 'N/A',
            'Joint Owner':     owner.is_joint_owner ? (isTamil ? 'ஆம்' : 'Yes') : (isTamil ? 'இல்லை' : 'No')
        };
        return _translateRow(r, engCols, tamCols);
    });

    return {
        title: data.query_type || `Survey ${data.survey_no || ''} Owners`,
        columns: tamCols,
        rows: rows,
        icon: '👤'
    };
}

/**
 * Renders the SIS officer's assigned jurisdiction as a table.
 * Hierarchy: District → Taluk → Town → Ward → Block
 */
function prepareJurisdictionTable(data, isTamil = false) {
    const j = data.jurisdiction || {};
    const district = j.district || {};
    const districtObj = typeof district === 'object' ? district : { name: district, code: '' };
    const distCode = districtObj.code || j.district_code || 'N/A';
    const distName = getDistrictName(districtObj.name || distCode);
    const taluk = j.taluk || {};
    const towns = j.towns || [];

    const rows = [];

    if (!towns.length) {
        rows.push({
            'Level': 'District',
            'Code / Number': distCode,
            'Name': distName,
            'Count': '—'
        });
        rows.push({
            'Level': 'Taluk',
            'Code / Number': '—',
            'Name': taluk.name || 'N/A',
            'Count': '—'
        });
    } else {
        rows.push({
            'Level': 'District',
            'Code / Number': distCode,
            'Name': distName,
            'Count': '—'
        });
        rows.push({
            'Level': 'Taluk',
            'Code / Number': '—',
            'Name': taluk.name || 'N/A',
            'Count': `${towns.length} Town(s)`
        });

        towns.forEach(town => {
            const wardCount = (town.wards || []).length;
            const blockCount = (town.wards || []).reduce(
                (sum, w) => sum + (w.blocks || []).length, 0
            );
            rows.push({
                'Level': 'Town',
                'Code / Number': '—',
                'Name': town.name || 'N/A',
                'Count': `${wardCount} Ward(s), ${blockCount} Block(s)`
            });
        });
    }

    if (j.survey_count !== undefined) {
        rows.push({
            'Level': 'Survey Numbers',
            'Code / Number': '—',
            'Name': '—',
            'Count': j.survey_count
        });
    }
    if (j.active_applications !== undefined) {
        rows.push({
            'Level': 'Active Applications',
            'Code / Number': '—',
            'Name': '—',
            'Count': j.active_applications
        });
    }

    return {
        title: data.query_type || 'Jurisdiction Summary',
        columns: _translateCols(['Level', 'Code / Number', 'Name', 'Count'], isTamil),
        rows: rows.map(r => _translateRow(r, ['Level', 'Code / Number', 'Name', 'Count'],
                                            _translateCols(['Level', 'Code / Number', 'Name', 'Count'], isTamil))),
        icon: '🗺️',
        disablePagination: true
    };
}

/**
 * Renders rejection history for an application.
 */
function prepareRejectionTable(data, isTamil = false) {
    const engCols = ['Rejected By', 'Reason', 'Rejected On', 'Resubmitted'];
    const tamCols = _translateCols(engCols, isTamil);
    const rows = (data.rejections || []).map(r => {
        const row = {
            'Rejected By': r.source || 'N/A',
            'Reason':      r.reason_text || 'N/A',
            'Rejected On': r.rejected_at ? new Date(r.rejected_at).toLocaleDateString() : 'N/A',
            'Resubmitted': r.resubmitted_at ? new Date(r.resubmitted_at).toLocaleDateString() : 'Not yet'
        };
        return _translateRow(row, engCols, tamCols);
    });

    return {
        title: data.query_type || `Rejections — ${data.application_number || ''}`,
        columns: tamCols,
        rows: rows,
        icon: '❌',
        disablePagination: true
    };
}

/**
 * Renders workflow stage history for an application.
 */
function prepareWorkflowTable(data, isTamil = false) {
    const engCols = ['Step', 'From Stage', 'To Stage', 'Date', 'Changed By', 'Note'];
    const tamCols = _translateCols(engCols, isTamil);
    const rows = (data.history || []).map((h, i) => {
        const row = {
            'Step':       i + 1,
            'From Stage': h.from_stage || '—',
            'To Stage':   h.to_stage || 'N/A',
            'Date':       h.changed_at ? new Date(h.changed_at).toLocaleDateString() : 'N/A',
            'Changed By': h.changed_by_name || 'System',
            'Note':       h.note || '—'
        };
        return _translateRow(row, engCols, tamCols);
    });

    return {
        title: data.query_type || `Workflow — ${data.application_number || ''}`,
        columns: tamCols,
        rows: rows,
        icon: '🔄',
        disablePagination: rows.length <= 10
    };
}

/**
 * Prepare workload table configuration
 */
function prepareWorkloadTable(data, isTamil = false) {
    const w = data.workload || {};
    const engCols = ['Metric', 'Count'];
    const tamCols = _translateCols(engCols, isTamil);
    const rows = [
        { 'Metric': isTamil ? 'மொத்த விண்ணப்பங்கள்'       : 'Total Applications',     'Count': w.total_applications || 0 },
        { 'Metric': isTamil ? 'நிலுவையில் உள்ள விண்ணப்பங்கள்' : 'Pending Applications',   'Count': w.pending_applications || 0 },
        { 'Metric': isTamil ? 'முடிந்த விண்ணப்பங்கள்'       : 'Completed Applications',  'Count': w.completed_applications || 0 }
    ].map(r => _translateRow(r, engCols, tamCols));

    return {
        title: data.query_type || 'Workload Summary',
        columns: tamCols,
        rows: rows,
        icon: '📊',
        disablePagination: true
    };
}

/**
 * Prepare single application and applicant details table configuration
 */
function prepareApplicationDetailTable(data, isTamil = false) {
    const fld = isTamil ? {
        serialNo:       'வரிசை எண் (Serial No)',
        appNo:          'விண்ணப்ப எண் (Application No)',
        userId:         'பயனர் ஐடி (User ID)',
        serviceCode:    'சேவை குறியீடு (Service Code)',
        distCode:       'மாவட்ட குறியீடு (District Code)',
        talukCode:      'தாலுகா குறியீடு (Taluk Code)',
        villageCode:    'கிராம குறியீடு (Village Code)',
        urbanUnitCode:  'நகர்ப்புற பிரிவு குறியீடு (Urban Unit)',
        appType:        'விண்ணப்ப வகை (Application Type)',
        wardCode:       'வார்டு குறியீடு (Ward Code)',
        blockCode:      'தொகுதி குறியீடு (Block Code)',
        appDate:        'விண்ணப்ப தேதி (Application Date)',
        status:         'விண்ணப்ப நிலை (Application Status)',
        surveyNo:       'சர்வே / கணக்கெண் (Survey No)',
        subdivNo:       'உட்பிரிவு எண் (Subdivision No)',
        currSubdivNo:   'தற்போதைய உட்பிரிவு (Current Subdivision)',
        pattaNo:        'பட்டா எண் (Patta No)',
        roleId:         'பங்கு ஐடி (Role ID)',
        sourceCode:     'மூல குறியீடு (Source Code)',
        canNumber:      'CAN எண் (CAN Number)',
        sourceName:     'மூல பெயர் (Source Name)',
        workflowState:  'பணிப்பாய்வு நிலை (Workflow State)',
        applicantName:  'விண்ணப்பதாரர் பெயர் (Applicant Name)',
        mobile:         'கைபேசி (Mobile)',
        address:        'முகவரி (Address)',
        fieldVisit:     'கள ஆய்வு (Field Visit)',
        overdue:        'காலதாமதம் (Overdue)',
        priority:       'முன்னுரிமை (Priority Flag)',
        reason:         'காரணம் (Declared Reason)',
        fieldCol:       'புலம் (Field)',
        detailCol:      'விவரங்கள் (Details)',
    } : {
        serialNo:       'Serial Number',
        appNo:          'Application Number',
        userId:         'User ID',
        serviceCode:    'Service Code',
        distCode:       'District Code',
        talukCode:      'Taluk Code',
        villageCode:    'Village Code',
        urbanUnitCode:  'Urban Unit Code',
        appType:        'Application Type',
        wardCode:       'Ward Code',
        blockCode:      'Block Code',
        appDate:        'Application Date',
        status:         'Application Status',
        surveyNo:       'Survey Number',
        subdivNo:       'Subdivision Number',
        currSubdivNo:   'Current Subdivision Number',
        pattaNo:        'Patta Number',
        roleId:         'Role ID',
        sourceCode:     'Source Code',
        canNumber:      'CAN Number',
        sourceName:     'Source Name',
        workflowState:  'Workflow State',
        applicantName:  'Applicant Name',
        mobile:         'Applicant Mobile',
        address:        'Applicant Address',
        fieldVisit:     'Field Visit Scheduled',
        overdue:        'Overdue',
        priority:       'Priority Flag',
        reason:         'Declared Reason',
        fieldCol:       'Field',
        detailCol:      'Details',
    };

    const formatDateVal = (d) => {
        if (!d) return 'N/A';
        try {
            const dt = new Date(d);
            return isNaN(dt.getTime()) ? String(d) : dt.toLocaleDateString();
        } catch {
            return String(d);
        }
    };

    const formatSubdivVal = (v) => {
        if (v === undefined || v === null || v === '') return null;
        if (typeof v === 'object') {
            if (v.proposed_sub_division_no) {
                return v.proposed_area_sqm ? `${v.proposed_sub_division_no} (${v.proposed_area_sqm} sq.m)` : v.proposed_sub_division_no;
            }
            if (v.sub_division_no) {
                return v.area_sqm ? `${v.sub_division_no} (${v.area_sqm} sq.m)` : v.sub_division_no;
            }
            return Object.values(v).filter(x => typeof x !== 'object').join(', ') || null;
        }
        return String(v);
    };

    const rawRows = [
        { [fld.fieldCol]: fld.serialNo,     [fld.detailCol]: data.serial_number !== undefined && data.serial_number !== null ? String(data.serial_number) : null },
        { [fld.fieldCol]: fld.appNo,        [fld.detailCol]: data.application_number || data.application_id || 'N/A' },
        { [fld.fieldCol]: fld.status,       [fld.detailCol]: data.application_status || data.status },
        { [fld.fieldCol]: fld.appType,      [fld.detailCol]: data.type || data.application_type },
        { [fld.fieldCol]: fld.workflowState,[fld.detailCol]: data.workflow_state || data.stage },
        { [fld.fieldCol]: fld.appDate,      [fld.detailCol]: formatDateVal(data.application_date || data.submission_date) },
        { [fld.fieldCol]: fld.surveyNo,     [fld.detailCol]: data.survey_number || data.survey_no || 'N/A' },
        { [fld.fieldCol]: fld.pattaNo,      [fld.detailCol]: data.patta_number },
        { [fld.fieldCol]: fld.subdivNo,     [fld.detailCol]: formatSubdivVal(data.subdivision_number || data.included_subdivisions) },
        { [fld.fieldCol]: fld.currSubdivNo, [fld.detailCol]: formatSubdivVal(data.current_subdivision_number) },
        { [fld.fieldCol]: fld.canNumber,    [fld.detailCol]: data.can_number || 'N/A' },
        { [fld.fieldCol]: fld.applicantName,[fld.detailCol]: data.applicant_name || 'N/A' },
        { [fld.fieldCol]: fld.mobile,       [fld.detailCol]: data.applicant_mobile || 'N/A' },
        { [fld.fieldCol]: fld.address,      [fld.detailCol]: data.applicant_address || 'N/A' },
        { [fld.fieldCol]: fld.distCode,     [fld.detailCol]: data.district_code },
        { [fld.fieldCol]: fld.talukCode,    [fld.detailCol]: data.taluk_code },
        { [fld.fieldCol]: fld.wardCode,     [fld.detailCol]: data.ward_code },
        { [fld.fieldCol]: fld.blockCode,    [fld.detailCol]: data.block_code },
        { [fld.fieldCol]: fld.urbanUnitCode,[fld.detailCol]: data.urban_unit_code },
        { [fld.fieldCol]: fld.fieldVisit,   [fld.detailCol]: data.field_visit_scheduled ? `${isTamil ? 'ஆம்' : 'Yes'} (${formatDateVal(data.field_visit_date)})` : (isTamil ? 'இல்லை' : 'No') },
        { [fld.fieldCol]: fld.overdue,      [fld.detailCol]: data.is_overdue ? (isTamil ? 'ஆம்' : 'Yes') : (isTamil ? 'இல்லை' : 'No') },
        { [fld.fieldCol]: fld.priority,     [fld.detailCol]: data.priority_flag ? (isTamil ? 'ஆம்' : 'Yes') : (isTamil ? 'இல்லை' : 'No') },
        { [fld.fieldCol]: fld.reason,       [fld.detailCol]: data.declared_reason ? formatDeclaredReason(data.declared_reason) : null }
    ];

    const rows = rawRows.filter(r => {
        const val = r[fld.detailCol];
        return val !== undefined && val !== null && val !== '' && val !== 'None';
    });

    return {
        title: data.query_type || 'Application & Applicant Details',
        columns: [fld.fieldCol, fld.detailCol],
        rows: rows,
        icon: '📋',
        disablePagination: true
    };
}

/**
 * Helper to build badge HTML for status columns
 */
function getStatusBadge(val) {
    if (!val) return 'N/A';
    const s = String(val).toUpperCase();
    
    if (s.includes('PENDING')) {
        return `<span class="status-badge status-pending">${escapeHtml(val)}</span>`;
    } else if (s.includes('APPROVED') || s === 'COMPLETED') {
        return `<span class="status-badge status-approved">${escapeHtml(val)}</span>`;
    } else if (s.includes('REJECTED')) {
        return `<span class="status-badge status-rejected">${escapeHtml(val)}</span>`;
    } else if (s === 'SCHEDULED' || s === 'RESCHEDULED') {
        return `<span class="status-badge status-scheduled">${escapeHtml(val)}</span>`;
    } else if (s === 'OVERDUE') {
        return `<span class="status-badge status-overdue">${escapeHtml(val)}</span>`;
    } else if (s === 'SUBMITTED') {
        return `<span class="status-badge status-submitted">${escapeHtml(val)}</span>`;
    } else if (s.includes('SIS')) {
        return `<span class="status-badge status-sis">${escapeHtml(val)}</span>`;
    } else if (s.includes('SD')) {
        return `<span class="status-badge status-sd">${escapeHtml(val)}</span>`;
    } else if (s.includes('DIS')) {
        return `<span class="status-badge status-dis">${escapeHtml(val)}</span>`;
    } else if (s.includes('TAHSILDAR')) {
        return `<span class="status-badge status-tahsildar">${escapeHtml(val)}</span>`;
    } else if (s === 'PATTA_ORDER_GENERATED') {
        return `<span class="status-badge status-approved">${escapeHtml(val)}</span>`;
    } else if (s === 'CLOSED') {
        return `<span class="status-badge status-closed">${escapeHtml(val)}</span>`;
    }
    
    return escapeHtml(val);
}

/**
 * Create HTML string for the table config
 */
function createTableHTML(config) {
    const { title, columns, rows, icon, disablePagination } = config;

    if (!rows || rows.length === 0) {
        return `
            <div class="data-table-card">
                <div class="data-table-header">
                    <div class="data-table-title-section">
                        <span class="data-table-icon">${icon || '📋'}</span>
                        <h3 class="data-table-title">${escapeHtml(title)}</h3>
                    </div>
                </div>
                <div class="data-table-empty">
                    <span>No records found.</span>
                </div>
            </div>
        `;
    }

    const tableId = 'table-' + Date.now() + '-' + Math.floor(Math.random() * 1000);
    const rowsPerPage = 10;
    const paginationEnabled = !disablePagination && rows.length > rowsPerPage;
    
    if (paginationEnabled) {
        tableStates.set(tableId, {
            currentPage: 1,
            totalPages: Math.ceil(rows.length / rowsPerPage),
            rowsPerPage: rowsPerPage,
            allRows: rows,
            columns: columns
        });
    }

    const visibleRows = paginationEnabled ? rows.slice(0, rowsPerPage) : rows;

    let headersHTML = columns.map(c => `<th>${escapeHtml(c)}</th>`).join('');
    
    let rowsHTML = visibleRows.map(row => {
        let cells = columns.map(col => {
            const val = row[col];
            if (col === 'Status' || col === 'Stage') {
                return `<td>${getStatusBadge(val)}</td>`;
            }
            const strVal = String(val !== undefined && val !== null ? val : 'N/A');
            const isAppNo = col === 'Number' || col === 'Application Number' || /^\d{4}\/\d+\/\d+\/\d+$/.test(strVal) || /^(ISD|NISD|MERGE)\/\w+\/\d+\/\d+$/i.test(strVal);
            if (isAppNo && strVal !== 'N/A') {
                return `<td><a href="javascript:void(0)" class="app-table-link" onclick="window.handleAppClick('${escapeHtml(strVal)}')" style="color:#2563eb;text-decoration:underline;cursor:pointer;font-weight:600;">${escapeHtml(strVal)}</a></td>`;
            }
            return `<td>${escapeHtml(strVal)}</td>`;
        }).join('');
        return `<tr>${cells}</tr>`;
    }).join('');

    let footerHTML = '';
    if (paginationEnabled) {
        const total = rows.length;
        const toIndex = Math.min(rowsPerPage, total);
        footerHTML = `
            <div class="data-table-footer">
                <div class="data-table-pagination">
                    <span class="pagination-info">Showing 1-${toIndex} of ${total}</span>
                    <div class="pagination-controls">
                        <button class="btn-pagination btn-prev" disabled>Prev</button>
                        <span class="pagination-page">Page 1 of ${Math.ceil(total / rowsPerPage)}</span>
                        <button class="btn-pagination btn-next">Next</button>
                    </div>
                </div>
            </div>
        `;
    }

    return `
        <div class="data-table-card" data-table-id="${tableId}">
            <div class="data-table-header">
                <div class="data-table-title-section">
                    <span class="data-table-icon">${icon}</span>
                    <h3 class="data-table-title">${escapeHtml(title)}</h3>
                    <span class="data-table-count">${rows.length} records</span>
                </div>
                <button class="btn-copy-table" onclick="window.copyTableData(this)">Copy</button>
            </div>
            <div class="data-table-container">
                <table class="data-table">
                    <thead>
                        <tr>${headersHTML}</tr>
                    </thead>
                    <tbody class="table-body-rows">
                        ${rowsHTML}
                    </tbody>
                </table>
            </div>
            <div class="data-table-scroll-hint">← scroll to view more →</div>
            ${footerHTML}
        </div>
    `;
}

/**
 * Setup event listeners for the table container
 */
function setupTableEventListeners(container) {
    if (!container) return;
    
    // Add event listeners for prev/next buttons
    const prevBtn = container.querySelector('.btn-prev');
    const nextBtn = container.querySelector('.btn-next');
    
    if (prevBtn) {
        prevBtn.addEventListener('click', handlePagination);
    }
    if (nextBtn) {
        nextBtn.addEventListener('click', handlePagination);
    }
}

/**
 * Handle table pagination events
 */
function handlePagination(event) {
    const button = event.target;
    const isNext = button.classList.contains('btn-next');
    const card = button.closest('.data-table-card');
    if (!card) return;
    
    const tableId = card.getAttribute('data-table-id');
    const state = tableStates.get(tableId);
    if (!state) return;
    
    // Calculate page change
    let newPage = state.currentPage;
    if (isNext) {
        newPage = Math.min(state.currentPage + 1, state.totalPages);
    } else {
        newPage = Math.max(state.currentPage - 1, 1);
    }
    
    if (newPage === state.currentPage) return;
    
    // Update state
    state.currentPage = newPage;
    tableStates.set(tableId, state);
    
    // Re-render table body rows
    const tbody = card.querySelector('.table-body-rows');
    const fromIdx = (newPage - 1) * state.rowsPerPage;
    const toIdx = Math.min(fromIdx + state.rowsPerPage, state.allRows.length);
    const visibleRows = state.allRows.slice(fromIdx, toIdx);
    
    tbody.innerHTML = visibleRows.map(row => {
        let cells = state.columns.map(col => {
            const val = row[col];
            if (col === 'Status' || col === 'Stage') {
                return `<td>${getStatusBadge(val)}</td>`;
            }
            return `<td>${escapeHtml(String(val !== undefined && val !== null ? val : 'N/A'))}</td>`;
        }).join('');
        return `<tr>${cells}</tr>`;
    }).join('');
    
    // Update pagination labels
    const info = card.querySelector('.pagination-info');
    if (info) {
        info.textContent = `Showing ${fromIdx + 1}-${toIdx} of ${state.allRows.length}`;
    }
    
    const pageText = card.querySelector('.pagination-page');
    if (pageText) {
        pageText.textContent = `Page ${newPage} of ${state.totalPages}`;
    }
    
    // Enable/disable buttons
    const prevBtn = card.querySelector('.btn-prev');
    const nextBtn = card.querySelector('.btn-next');
    
    if (prevBtn) prevBtn.disabled = newPage === 1;
    if (nextBtn) nextBtn.disabled = newPage === state.totalPages;
}

/**
 * Copy data table contents as TSV
 */
window.copyTableData = function(button) {
    const card = button.closest('.data-table-card');
    if (!card) return;
    
    const table = card.querySelector('table');
    if (!table) return;
    
    let tsv = [];
    // Get headers
    const headers = Array.from(table.querySelectorAll('thead th')).map(th => th.textContent.trim());
    tsv.push(headers.join('\t'));
    
    // Get all rows from stored state if available, or fall back to DOM
    const tableId = card.getAttribute('data-table-id');
    const state = tableStates.get(tableId);
    
    if (state && state.allRows && state.columns) {
        state.allRows.forEach(row => {
            const rowData = state.columns.map(col => String(row[col] !== undefined && row[col] !== null ? row[col] : 'N/A'));
            tsv.push(rowData.join('\t'));
        });
    } else {
        const rows = Array.from(table.querySelectorAll('tbody tr'));
        rows.forEach(tr => {
            const cells = Array.from(tr.querySelectorAll('td')).map(td => td.textContent.trim());
            tsv.push(cells.join('\t'));
        });
    }
    
    const textToCopy = tsv.join('\n');
    navigator.clipboard.writeText(textToCopy).then(() => {
        const originalText = button.textContent;
        button.textContent = 'Copied!';
        button.style.borderColor = 'var(--primary, #1E40AF)';
        button.style.color = 'var(--primary, #1E40AF)';
        
        setTimeout(() => {
            button.textContent = originalText;
            button.style.borderColor = '';
            button.style.color = '';
        }, 2000);
    }).catch(err => {
        console.error('Failed to copy table data:', err);
    });
};

/**
 * Format a declared_reason enum value into a human-readable label.
 * e.g. "gift_deed" → "Gift Deed", "inheritance" → "Inheritance"
 */
function formatDeclaredReason(value) {
    if (!value || value === 'N/A') return value || 'N/A';
    const map = {
        'sale':        'Sale',
        'inheritance': 'Inheritance',
        'partition':   'Partition',
        'gift_deed':   'Gift Deed',
        'court_order': 'Court Order',
        'government':  'Government Acquisition',
        'exchange':    'Exchange',
        'will':        'Will / Testament',
    };
    const key = String(value).toLowerCase().trim();
    return map[key] || String(value).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

/**
 * Format a Date value to localized string
 */
function formatDateVal(d) {
    if (!d) return 'N/A';
    try {
        const dt = new Date(d);
        return isNaN(dt.getTime()) ? String(d) : dt.toLocaleDateString();
    } catch {
        return String(d);
    }
}

/**
 * Escape HTML special characters
 */
function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
