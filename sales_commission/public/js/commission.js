// Live commission on Sales Order and Sales Invoice forms. The server works every figure out, the form only shows them.
(function () {
	const DATE_FIELD = { "Sales Order": "transaction_date", "Sales Invoice": "posting_date" };
	const LINE_FIELDS = [
		"commission_classification",
		"commission_percent",
		"commission_factor",
		"commission_amount",
		"base_commission_amount",
		"target_multiplier",
	];
	const TOTAL_FIELDS = ["total_commission_amount", "base_total_commission_amount"];
	const DEBOUNCE_MS = 400;

	function show_line(frm, line) {
		const row = (locals[frm.doc.doctype + " Item"] || {})[line.name];
		if (!row) return;
		const grid = frm.fields_dict.items.grid;
		const grid_row = grid.grid_rows_by_docname && grid.grid_rows_by_docname[row.name];
		LINE_FIELDS.forEach((field) => {
			row[field] = line[field];
			if (grid_row) grid_row.refresh_field(field);
		});
	}

	function show_incentives(frm, incentives) {
		const rows = frm.doc.sales_team || [];
		const grid = frm.fields_dict.sales_team && frm.fields_dict.sales_team.grid;
		rows.forEach((row, index) => {
			if (index >= incentives.length) return;
			row.incentives = incentives[index];
			const grid_row =
				grid && grid.grid_rows_by_docname && grid.grid_rows_by_docname[row.name];
			if (grid_row) grid_row.refresh_field("incentives");
		});
	}

	function show_result(frm, result) {
		(result.lines || []).forEach((line) => show_line(frm, line));
		// the answer belongs to the rows that were sent, a changed team makes it stale
		if ((result.incentives || []).length === (frm.doc.sales_team || []).length) {
			show_incentives(frm, result.incentives);
		}
		TOTAL_FIELDS.forEach((field) => {
			frm.doc[field] = result[field];
			frm.refresh_field(field);
		});
	}

	function run_preview(frm) {
		if (frm.doc.docstatus !== 0 || !(frm.doc.items || []).some((r) => r.item_code)) return;
		const docname = frm.doc.name;
		const request = (frm.commission_request = (frm.commission_request || 0) + 1);
		frappe.call({
			method: "sales_commission.utils.preview.get_line_commission",
			args: { doc: frm.doc },
			silent: true,
			callback: (r) => {
				// a newer request, another document or a saved one makes this answer stale
				if (
					request !== frm.commission_request ||
					frm.doc.name !== docname ||
					frm.doc.docstatus !== 0
				)
					return;
				show_result(frm, r.message || {});
			},
			error: (error) => console.error("Commission preview failed", error),
		});
	}

	function request_preview(frm) {
		if (!frm.commission_debounced)
			frm.commission_debounced = frappe.utils.debounce(() => run_preview(frm), DEBOUNCE_MS);
		frm.commission_debounced();
	}

	// run `after` once ERPNext has finished the original handler (item details arrive by ajax)
	function wrap_handler(controller, name, after) {
		const original = controller[name];
		if (!original) return;
		controller[name] = function (...args) {
			const result = original.apply(this, args);
			return Promise.resolve(result).then(() => frappe.after_ajax(after));
		};
	}

	function wrap_controller(frm) {
		const controller = frm.cscript;
		if (!controller || controller.commission_wrapped) return;
		wrap_handler(controller, "calculate_taxes_and_totals", () => request_preview(frm));
		wrap_handler(controller, "item_code", () => request_preview(frm));
		// ERPNext works incentives out from the Commission Rate, the preview is the only writer here
		controller.calculate_incentive = function () {};
		controller.commission_wrapped = true;
	}

	// every new document shows its figures when it opens (also from Create, Return and Duplicate)
	function preview_new_document(frm) {
		if (!frm.is_new() || frm.commission_previewed === frm.doc.name) return;
		frm.commission_previewed = frm.doc.name;
		frm.commission_request = (frm.commission_request || 0) + 1;
		request_preview(frm);
	}

	// Sales Team row events (incl. add / remove) fire on the child doctype; Customer also uses that table
	function request_team_preview(frm) {
		if (DATE_FIELD[frm.doc.doctype]) request_preview(frm);
	}

	function make_team_handlers() {
		frappe.ui.form.on("Sales Team", {
			sales_person: request_team_preview,
			allocated_percentage: request_team_preview,
			sales_team_add: request_team_preview,
			sales_team_remove: request_team_preview,
		});
	}

	// after a draft is saved, show the commission of each line so the user can check it
	function show_commission_summary(frm) {
		if (frm.doc.docstatus !== 0 || frm.doc.is_return) return;
		// commission and the annual target belong to the salesperson, so there is nothing to show without one
		if (!(frm.doc.sales_team || []).some((row) => row.sales_person)) return;
		const lines = (frm.doc.items || [])
			.filter((row) => row.commission_classification)
			.map((row) => ({
				row_no: row.idx,
				item_code: row.item_code,
				item_group: row.item_group,
				classification: row.commission_classification,
				factor: row.commission_factor,
				commission_amount: row.commission_amount,
				currency: frm.doc.currency,
			}));
		if (!lines.length) return;
		const dialog = new frappe.ui.Dialog({
			title: __("Sales Commission"),
			indicator: "blue",
			size: "extra-large",
			fields: [
				{
					fieldtype: "Table",
					fieldname: "lines",
					label: __("Commission per Item"),
					read_only: 1,
					cannot_add_rows: 1,
					cannot_delete_rows: 1,
					in_place_edit: false,
					data: lines,
					get_data: () => lines,
					fields: [
						{
							fieldtype: "Int",
							fieldname: "row_no",
							label: __("Row"),
							in_list_view: 1,
							columns: 1,
							read_only: 1,
						},
						{
							fieldtype: "Link",
							fieldname: "item_code",
							label: __("Item"),
							options: "Item",
							in_list_view: 1,
							columns: 2,
							read_only: 1,
						},
						{
							fieldtype: "Link",
							fieldname: "item_group",
							label: __("Item Group"),
							options: "Item Group",
							in_list_view: 1,
							columns: 2,
							read_only: 1,
						},
						{
							fieldtype: "Data",
							fieldname: "classification",
							label: __("Classification"),
							in_list_view: 1,
							columns: 2,
							read_only: 1,
						},
						{
							fieldtype: "Float",
							fieldname: "factor",
							label: __("Classification Factor"),
							in_list_view: 1,
							columns: 2,
							read_only: 1,
						},
						{
							fieldtype: "Currency",
							fieldname: "commission_amount",
							label: __("Commission Amount"),
							options: "currency",
							in_list_view: 1,
							columns: 1,
							read_only: 1,
						},
						{
							fieldtype: "Link",
							fieldname: "currency",
							label: __("Currency"),
							options: "Currency",
							hidden: 1,
						},
					],
				},
				{ fieldtype: "Section Break" },
				{
					fieldtype: "Currency",
					fieldname: "total_commission_amount",
					label: __("Total Commission Amount"),
					options: frm.doc.currency,
					default: frm.doc.total_commission_amount,
					read_only: 1,
				},
				{ fieldtype: "Column Break" },
				{
					fieldtype: "Currency",
					fieldname: "base_total_commission_amount",
					label: __("Total Commission Amount (Company Currency)"),
					options: erpnext.get_currency(frm.doc.company),
					default: frm.doc.base_total_commission_amount,
					read_only: 1,
				},
			],
		});
		dialog.show();
		show_as_read_only(dialog.fields_dict.lines.grid);
	}

	// fixed shares of the dialog width, so the table always fits without a sideways scrollbar
	const COLUMN_SHARES = {
		row_no: 1,
		item_code: 2,
		item_group: 2,
		classification: 2,
		factor: 2,
		commission_amount: 2,
	};

	// a grid inside a dialog is always editable in Frappe: make it static and drop the row selectors
	function show_as_read_only(grid) {
		grid.static_rows = true;
		grid.refresh();
		// selector, row number, edit and column-settings cells; only the data cells carry a data-fieldname
		grid.wrapper.find(".data-row > .col").not("[data-fieldname]").hide();
		grid.wrapper
			.find(".form-grid-container, .form-grid")
			.css({ "overflow-x": "hidden", "min-width": "0" });
		grid.wrapper.find(".data-row").css({ width: "auto", "min-width": "0" });
		grid.wrapper.find(".data-row > [data-fieldname]").each((i, cell) => {
			const share = COLUMN_SHARES[cell.dataset.fieldname] || 1;
			$(cell).css({
				flex: `${share} 1 0`,
				width: "auto",
				"min-width": "0",
				"max-width": "none",
			});
		});
		grid.wrapper.find(".grid-footer").hide();
	}

	function make_handlers(doctype) {
		const handlers = {
			setup: wrap_controller,
			onload: wrap_controller,
			refresh(frm) {
				wrap_controller(frm);
				preview_new_document(frm);
			},
			customer: request_preview,
			company: request_preview,
			order_type: request_preview,
			is_return: request_preview,
			return_against: request_preview,
			after_save: show_commission_summary,
		};
		handlers[DATE_FIELD[doctype]] = request_preview;
		frappe.ui.form.on(doctype, handlers);
	}

	// hooks load this file for Sales Order and for Sales Invoice; frappe.ui.form.on adds handlers, so bind once
	frappe.provide("sales_commission");
	if (sales_commission.commission_bound) return;
	sales_commission.commission_bound = true;
	Object.keys(DATE_FIELD).forEach(make_handlers);
	make_team_handlers();
})();
