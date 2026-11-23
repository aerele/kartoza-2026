// Copyright (c) 2026, Aerele and contributors
// For license information, please see license.txt

frappe.provide("kartuza_2026");

// The first salesperson added to a Sales Team gets the whole contribution.
kartuza_2026.default_first_contribution = function (frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if ((frm.doc.sales_team || []).length === 1 && !row.allocated_percentage) {
		frappe.model.set_value(cdt, cdn, "allocated_percentage", 100);
	}
};

// Frappe fires <table>_add on the child doctype, so the handler is bound to "Sales Team" (shared by
// Customer, Sales Order and Sales Invoice). The guard keeps it to one binding when several forms load this file.
if (!kartuza_2026.sales_team_bound) {
	kartuza_2026.sales_team_bound = true;
	frappe.ui.form.on("Sales Team", {
		sales_team_add: kartuza_2026.default_first_contribution,
	});
}
