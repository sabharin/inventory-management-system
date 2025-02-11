import json
import os
import sqlite3
from pathlib import Path

from flask import Flask, redirect, render_template, request, g, url_for

DATABASE_NAME = "inventory.sqlite"
_DATABASE_PATH = Path(__file__).parent.parent / DATABASE_NAME
VIEWS = {
    "Summary": "/",
    "Stock": "/product",
    "Locations": "/location",
}
EMPTY_SYMBOLS = {"", " ", None}

app = Flask(__name__)
app.config["DATABASE"] = os.environ.get("DATABASE_NAME") or _DATABASE_PATH.resolve()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"], detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_database():
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                prod_id INTEGER PRIMARY KEY AUTOINCREMENT,
                prod_name TEXT UNIQUE NOT NULL,
                prod_quantity INTEGER NOT NULL,
                unallocated_quantity INTEGER
            );
            CREATE TABLE IF NOT EXISTS location (
                loc_id INTEGER PRIMARY KEY AUTOINCREMENT,
                loc_name TEXT UNIQUE NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS default_prod_qty_to_unalloc_qty
            AFTER INSERT ON products
            FOR EACH ROW WHEN NEW.unallocated_quantity IS NULL
            BEGIN
                UPDATE products SET unallocated_quantity = NEW.prod_quantity WHERE rowid = NEW.rowid;
            END;
            """
        )


app.init_db = init_database


@app.route("/")
def summary():
    db = get_db()
    warehouse = db.execute("SELECT * FROM location").fetchall()
    products = db.execute("SELECT * FROM products").fetchall()
    q_data = db.execute(
        "SELECT prod_name, unallocated_quantity, prod_quantity FROM products"
    ).fetchall()

    return render_template(
        "index.jinja", link=VIEWS, title="Summary", warehouses=warehouse, products=products, summary=q_data
    )


@app.route("/product", methods=["POST", "GET"])
def product():
    db = get_db()
    if request.method == "POST":
        prod_name, quantity = request.form.get("prod_name"), request.form.get("prod_quantity")
        if prod_name and quantity and prod_name.strip():
            try:
                quantity = int(quantity)
                db.execute(
                    "INSERT INTO products (prod_name, prod_quantity) VALUES (?, ?)",
                    (prod_name, quantity),
                )
                db.commit()
            except sqlite3.IntegrityError:
                pass  # Handle duplicate entry properly
            return redirect(VIEWS["Stock"])
    
    products = db.execute("SELECT * FROM products").fetchall()
    return render_template("product.jinja", link=VIEWS, products=products, title="Stock")


@app.route("/location", methods=["POST", "GET"])
def location():
    db = get_db()
    if request.method == "POST":
        warehouse_name = request.form.get("warehouse_name")
        if warehouse_name and warehouse_name.strip():
            db.execute("INSERT INTO location (loc_name) VALUES (?)", (warehouse_name,))
            db.commit()
            return redirect(VIEWS["Locations"])
    
    warehouse_data = db.execute("SELECT * FROM location").fetchall()
    return render_template("location.jinja", link=VIEWS, warehouses=warehouse_data, title="Locations")


@app.route("/delete")
def delete():
    delete_record_type = request.args.get("type")
    db = get_db()
    
    if delete_record_type == "product":
        product_id = request.args.get("prod_id")
        if product_id:
            db.execute("DELETE FROM products WHERE prod_id = ?", (product_id,))
            db.commit()
        return redirect(VIEWS["Stock"])

    if delete_record_type == "location":
        location_id = request.args.get("loc_id")
        if location_id:
            db.execute("DELETE FROM location WHERE loc_id = ?", (location_id,))
            db.commit()
        return redirect(VIEWS["Locations"])
    
    return redirect(VIEWS["Summary"])

@app.route('/reduce/<int:prod_id>/<string:type>', methods=['POST'])
def reduce(prod_id, type):
    """Reduce stock quantity from a specific product."""

    db = get_db()
    
    # Fetch current stock
    current_stock = db.execute(
        "SELECT prod_quantity, unallocated_quantity FROM products WHERE prod_id = ?", (prod_id,)
    ).fetchone()
    
    if not current_stock:
        return {"error": "Product not found"}
    
    prod_quantity, unallocated_quantity = current_stock
    
    if 1 > prod_quantity:
        return {"error": "Not enough stock available"}
    
    # Update stock
    new_quantity = prod_quantity - 1
    new_unallocated = max(0, unallocated_quantity - 1)  # Ensure it doesn't go negative

    db.execute(
        "UPDATE products SET prod_quantity = ?, unallocated_quantity = ? WHERE prod_id = ?",
        (new_quantity, new_unallocated, prod_id),
    )
    db.commit()
    
    return redirect(VIEWS["Summary"])  # Redirect back to the index page

@app.route('/add/<int:prod_id>/<string:type>', methods=['POST'])
def add(prod_id, type):
    """Increase stock quantity for a specific product."""
    db = get_db()

    # Fetch current stock
    current_stock = db.execute(
        "SELECT prod_quantity, unallocated_quantity FROM products WHERE prod_id = ?", (prod_id,)
    ).fetchone()

    if not current_stock:
        return {"error": "Product not found"}

    prod_quantity, unallocated_quantity = current_stock

    # Update stock
    new_quantity = prod_quantity + 1
    new_unallocated = unallocated_quantity + 1

    db.execute(
        "UPDATE products SET prod_quantity = ?, unallocated_quantity = ? WHERE prod_id = ?",
        (new_quantity, new_unallocated, prod_id),
    )
    db.commit()
    
    return redirect(VIEWS["Summary"])  # Redirect back to the index page

@app.route("/edit", methods=["POST"])
def edit():
    edit_record_type = request.args.get("type")
    db = get_db()
    
    if edit_record_type == "location":
        loc_id, loc_name = request.form.get("loc_id"), request.form.get("loc_name")
        if loc_name and loc_name.strip():
            db.execute("UPDATE location SET loc_name = ? WHERE loc_id = ?", (loc_name, loc_id))
            db.commit()
        return redirect(VIEWS["Locations"])

    if edit_record_type == "product":
        prod_id, prod_name, prod_quantity = (
            request.form.get("prod_id"),
            request.form.get("prod_name"),
            request.form.get("prod_quantity"),
        )
        if prod_name and prod_name.strip():
            db.execute("UPDATE products SET prod_name = ? WHERE prod_id = ?", (prod_name, prod_id))
        if prod_quantity:
            prod_quantity = int(prod_quantity)
            old_prod_quantity = db.execute(
                "SELECT prod_quantity FROM products WHERE prod_id = ?", (prod_id,)
            ).fetchone()[0]
            db.execute(
                "UPDATE products SET prod_quantity = ?, unallocated_quantity = unallocated_quantity + ? - ? WHERE prod_id = ?",
                (prod_quantity, prod_quantity, old_prod_quantity, prod_id),
            )
        db.commit()
        return redirect(VIEWS["Stock"])
    
    return redirect(VIEWS["Summary"])


if __name__ == "__main__":
    with app.app_context():
        app.init_db()
    app.run(debug=True, port=8001)
