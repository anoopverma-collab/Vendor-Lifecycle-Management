// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vendor Background Check Reference", {
	refresh(frm) {
		toggle_ratings_add_row(frm);
		if (frm.__last_rating_template === undefined) {
			frm.__last_rating_template = frm.doc.rating_template;
		}
	},
	rating_template(frm) {
		// A programmatic revert (see below) re-fires this same handler —
		// skip it once, rather than asking the user to confirm a change
		// they already declined.
		if (frm.__reverting_rating_template) {
			frm.__reverting_rating_template = false;
			return;
		}

		const new_template = frm.doc.rating_template;
		const previous_template = frm.__last_rating_template;

		const apply_change = () => {
			frm.__last_rating_template = new_template;
			toggle_ratings_add_row(frm);
			if (new_template) {
				frm.call("load_ratings_from_template").then(() => frm.refresh_field("ratings"));
			}
		};

		if (!new_template) {
			// Cleared, not switched to another template — nothing gets
			// wiped, just unlocked, so no confirmation needed.
			apply_change();
			return;
		}

		const has_existing_data = (frm.doc.ratings || []).some((row) => row.criteria || row.score);
		if (!has_existing_data) {
			apply_change();
			return;
		}

		frappe.confirm(
			__(
				"Picking this Rating Criteria Template will replace the Ratings table below with the" +
					" template's criteria — anything already entered here, including scores, will be lost." +
					" Continue?"
			),
			apply_change,
			() => {
				frm.__reverting_rating_template = true;
				frm.set_value("rating_template", previous_template);
			}
		);
	},

	before_submit(frm) {
		// Saving always recomputes this Reference's Result fresh anyway
		// (see validate()), so the committed value is never actually at
		// risk — this is purely a courtesy heads-up in case what's about
		// to be submitted differs from what's currently on screen (e.g. a
		// Settings value changed since this was last saved).
		frappe.validated = false;
		return frm.call("check_result_is_fresh").then((r) => {
			if (r.message && r.message.is_fresh) {
				frappe.validated = true;
				return;
			}
			frappe.msgprint({
				title: __("Recalculation Needed"),
				indicator: "orange",
				message: __(
					"This Reference's Result may be about to change — something it depends on (its Rating" +
						" Criteria Template, or a Vendor Lifecycle Settings value) has changed since it was" +
						" last saved. Please resave to see the current Result, then submit again."
				),
				primary_action: {
					label: __("Resave"),
					action() {
						// frappe.msgprint's primary_action callback receives
						// the dialog's (empty, field-less) get_values() as
						// its argument, not the dialog itself — the shared
						// message dialog is closed via frappe.hide_msgprint().
						frappe.hide_msgprint();
						// What went stale here is something this doc depends on
						// (its Rating Criteria Template, a Settings value) — not a
						// field on this form itself, so frm.save() would otherwise
						// see no dirty fields and abort with "No changes in
						// document" without ever reaching the server. frm.dirty()
						// forces it through so the server actually recomputes.
						frm.dirty();
						frm.save();
					},
				},
			});
		});
	},
});

function toggle_ratings_add_row(frm) {
	// A template's criteria are meant to be used as-is — once one is
	// picked, rows can no longer be added, removed, or duplicated by hand;
	// clearing the template unlocks the grid again.
	//
	// Must go through set_df_property (not a direct grid.cannot_add_rows /
	// grid.cannot_delete_rows assignment) — the grid's own Delete and
	// Duplicate button visibility checks (refresh_remove_rows_button /
	// refresh_duplicate_rows_button in Frappe's grid.js) read these two
	// flags off the field's docfield object (this.df), not off the grid
	// instance, so setting them directly on the grid instance is silently
	// ignored by exactly those two buttons (same fix as on Vendor
	// Background Check's compliance_checks table).
	const locked = !!frm.doc.rating_template;
	frm.set_df_property("ratings", "cannot_add_rows", locked);
	frm.set_df_property("ratings", "cannot_delete_rows", locked);
	frm.fields_dict.ratings.grid.refresh();
}
