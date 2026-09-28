// Tables: one Tabulator table (vendor/tabulator, loaded by index.html as the global Tabulator)
// from columns and rows. Every column sorts on a header click and filters in the header;
// the sort and the filters are remembered per table in this browser.
import { store } from "../lib.js";

function saved(key) { try { return JSON.parse(store("river.table." + key) || "{}"); } catch (e) { return {}; } }

// A column is a Tabulator column definition plus:
//   filter: "text" (default: a text box, matches part of the value), "select" (a list of the
//           column's values), or false;
//   html:   (row) => HTML for the cell, for example a chip from chip.js; the column still sorts
//           and filters on its field.
function column({ filter = "text", html, ...c }) {
  const col = { headerSort: true, ...c };
  if (html) col.formatter = (cell) => html(cell.getData());
  if (filter === "select") Object.assign(col, { headerFilter: "list", headerFilterFunc: "=",
    headerFilterParams: { valuesLookup: "active", clearable: true, sort: "asc" } });
  else if (filter === "text") Object.assign(col, { headerFilter: "input", headerFilterPlaceholder: "filter" });
  return col;
}

// makeTable(el, { key, columns, index, sort, onRow, placeholder, height, ...options }) returns { set(rows), tabulator };
// options go to Tabulator as they are (for example groupBy and groupHeader).
//   key:    names the table for the remembered sort and filters;
//   index:  the field that names a row (default "id"): set() updates rows in place by it;
//   sort:   the first sort, as [{ column: field, dir: "asc" | "desc" }], until the person picks one;
//   onRow:  (row) => ... when someone clicks a row outside its links, buttons and inputs.
// Call set(rows) on every refresh: the same rows do nothing, and changed rows update in place,
// so the sort, the filters, the scroll position and any selection stay.
export function makeTable(el, { key, columns, index = "id", sort = [], onRow, placeholder = "Nothing here.", height, ...options } = {}) {
  const keep = saved(key);
  const t = new window.Tabulator(el, {
    index, height, placeholder, data: [], layout: "fitColumns", ...options,
    columns: columns.map(column),
    initialSort: keep.sort || sort,
    initialHeaderFilter: keep.filter || [],
  });
  const built = new Promise((r) => t.on("tableBuilt", r));
  const remember = () => store("river.table." + key, JSON.stringify({
    sort: t.getSorters().map((s) => ({ column: s.field, dir: s.dir })),
    filter: t.getHeaderFilters().map((f) => ({ field: f.field, value: f.value })),
  }));
  built.then(() => { t.on("dataSorted", remember); t.on("dataFiltered", remember); });
  if (onRow) t.on("rowClick", (e, row) => { if (!e.target.closest("a, button, input, select, textarea, .link")) onRow(row.getData()); });

  let last = null, loaded = false;
  async function set(rows) {
    const sig = JSON.stringify(rows);
    if (sig === last) return;
    last = sig;
    await built;
    if (!loaded) { loaded = true; return t.setData(rows); }
    const ids = new Set(rows.map((r) => r[index]));
    const gone = t.getData().map((r) => r[index]).filter((id) => !ids.has(id));
    if (gone.length) t.deleteRow(gone);
    await t.updateOrAddData(rows);
    // Changed and new rows take their place in the current sort and filters.
    t.setSort(t.getSorters().map((s) => ({ column: s.field, dir: s.dir })));
    t.refreshFilter();
  }
  return { set, tabulator: t };
}
