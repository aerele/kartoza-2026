// Live commission on Sales Order and Sales Invoice forms. The server works every figure out, the form only shows them.
(function () {
	const DATE_FIELD = { "Sales Order": "transaction_date", "Sales Invoice": "posting_date" };
	const LINE_FIELDS = [
		"commission_classification",
		"commission_percent",
		"commission_factor",
		"commission_note",
		"commission_amount",
		"base_commission_amount",
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

	function show_result(frm, result) {
		(result.lines || []).forEach((line) => show_line(frm, line));
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
				if (request !== frm.commission_request || frm.doc.name !== docname || frm.doc.docstatus !== 0) return;
				show_result(frm, r.message || {});
			},
			error: (error) => console.error("Commission preview failed", error),
		});
	}

	function request_preview(frm) {
		if (!frm.commission_debounced) frm.commission_debounced = frappe.utils.debounce(() => run_preview(frm), DEBOUNCE_MS);
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
		controller.commission_wrapped = true;
	}

	// every new document shows its figures when it opens (also from Create, Return and Duplicate)
	function preview_new_document(frm) {
		if (!frm.is_new() || frm.commission_previewed === frm.doc.name) return;
		frm.commission_previewed = frm.doc.name;
		frm.commission_request = (frm.commission_request || 0) + 1;
		request_preview(frm);
	}

	function lock_override(frm) {
		if (frm.doc.doctype !== "Sales Invoice" || !frm.doc.is_return) return;
		const allowed = frappe.user.has_role("System Manager");
		frm.set_df_property("override_deduction", "read_only", allowed ? 0 : 1);
	}

	function make_handlers(doctype) {
		const handlers = {
			setup: wrap_controller,
			onload: wrap_controller,
			refresh(frm) {
				wrap_controller(frm);
				lock_override(frm);
				preview_new_document(frm);
			},
			customer: request_preview,
			company: request_preview,
			order_type: request_preview,
			is_return: request_preview,
			return_against: request_preview,
		};
		handlers[DATE_FIELD[doctype]] = request_preview;
		frappe.ui.form.on(doctype, handlers);
	}

	Object.keys(DATE_FIELD).forEach(make_handlers);
})();
