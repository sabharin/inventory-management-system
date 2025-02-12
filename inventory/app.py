import json
import os
import sqlite3
from pathlib import Path

from flask import Flask, redirect, render_template, request, g, jsonify

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
            CREATE TABLE IF NOT EXISTS locations (
                loc_id INTEGER PRIMARY KEY AUTOINCREMENT,
                loc_name TEXT UNIQUE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS products (
                prod_id INTEGER PRIMARY KEY AUTOINCREMENT,
                prod_name TEXT UNIQUE NOT NULL,
                prod_quantity INTEGER NOT NULL,
                loc_id INTEGER,
                FOREIGN KEY (loc_id) REFERENCES locations (loc_id)
            );
            """
        )


app.init_db = init_database


@app.route("/")
def summary():
    db = get_db()
    products = db.execute(
        "SELECT prod_id, prod_name, prod_quantity, loc_id FROM products"
    ).fetchall()
    locations = db.execute("SELECT loc_id, loc_name FROM locations").fetchall()

    return render_template(
        "index.jinja", 
        link=VIEWS, 
        title="Summary", 
        products=products, 
        locations=locations
    )

@app.route('/get-products-by-location')
def get_products_by_location_ajax():
    location_id = request.args.get('location_id')
    db = get_db()

    if location_id:
        # Fetch products for the given location
        products = db.execute(
            "SELECT prod_id, prod_name, prod_quantity FROM products WHERE loc_id = ?",
            (location_id,)
        ).fetchall()

        # Convert the products to a list of dictionaries
        products_list = [dict(product) for product in products]
        return jsonify({"products": products_list})
    else:
        return jsonify({"error": "Location ID is required"}), 400

@app.route('/location/<int:location_id>')
def get_products_by_location(location_id):
    db = get_db()
    
    # Fetch products for the given location
    products = db.execute(
        "SELECT prod_id, prod_name, prod_quantity, loc_id FROM products WHERE loc_id = ?",
        (location_id,)
    ).fetchall()
    
    # Fetch all locations for the dropdown or other UI elements
    locations = db.execute("SELECT loc_id, loc_name FROM locations").fetchall()
    
    # Fetch the location name for the selected location
    location_name = db.execute(
        "SELECT loc_name FROM locations WHERE loc_id = ?",
        (location_id,)
    ).fetchone()
    
    if not location_name:
        return {"error": "Location not found"}, 404
    
    return render_template(
        "index.jinja", 
        link=VIEWS, 
        title=f"Products in {location_name['loc_name']}", 
        products=products, 
        locations=locations,
        selected_location_id=location_id  # Pass the selected location ID to the template
    )

@app.route("/product", methods=["POST", "GET"])
def product():
    db = get_db()
    if request.method == "POST":
        prod_name = request.form.get("prod_name")
        quantity = request.form.get("prod_quantity")
        loc_id = request.form.get("loc_id")

        if prod_name and quantity and prod_name.strip():
            try:
                quantity = int(quantity)
                loc_id = int(loc_id) if loc_id else None

                # Validate loc_id against the locations table
                if loc_id:
                    location_exists = db.execute(
                        "SELECT loc_id FROM locations WHERE loc_id = ?", (loc_id,)
                    ).fetchone()
                    if not location_exists:
                        return {"error": "Invalid location ID"}, 400

                db.execute(
                    "INSERT INTO products (prod_name, prod_quantity, loc_id) VALUES (?, ?, ?)",
                    (prod_name, quantity, loc_id),
                )
                db.commit()
            except sqlite3.IntegrityError:
                return {"error": "Product name must be unique"}, 400
            return redirect(VIEWS["Stock"])
    
    products = db.execute(
        "SELECT prod_id, prod_name, prod_quantity, loc_id FROM products"
    ).fetchall()
    locations = db.execute("SELECT loc_id, loc_name FROM locations").fetchall()

    return render_template(
        "product.jinja", link=VIEWS, products=products, locations=locations, title="Stock"
    )


@app.route("/location", methods=["POST", "GET"])
def location():
    db = get_db()
    if request.method == "POST":
        location_name = request.form.get("location_name")
        if location_name and location_name.strip():
            try:
                db.execute("INSERT INTO locations (loc_name) VALUES (?)", (location_name,))
                db.commit()
            except sqlite3.IntegrityError:
                return {"error": "Location name must be unique"}, 400
            return redirect(VIEWS["Locations"])
    
    locations = db.execute("SELECT loc_id, loc_name FROM locations").fetchall()
    return render_template("location.jinja", link=VIEWS, locations=locations, title="Locations")


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
            # Check if the location is being used by any product
            product_using_location = db.execute(
                "SELECT prod_id FROM products WHERE loc_id = ?", (location_id,)
            ).fetchone()
            if product_using_location:
                return {"error": "Location is in use and cannot be deleted"}, 400

            db.execute("DELETE FROM locations WHERE loc_id = ?", (location_id,))
            db.commit()
        return redirect(VIEWS["Locations"])
    
    return redirect(VIEWS["Summary"])


@app.route('/reduce/<int:prod_id>/<string:type>', methods=['POST'])
def reduce(prod_id, type):
    """Reduce stock quantity from a specific product."""

    db = get_db()
    
    # Fetch current stock
    current_stock = db.execute(
        "SELECT prod_quantity FROM products WHERE prod_id = ?", (prod_id,)
    ).fetchone()
    
    if not current_stock:
        return {"error": "Product not found"}, 404
    
    prod_quantity = current_stock["prod_quantity"]
    
    if 1 > prod_quantity:
        return {"error": "Not enough stock available"}, 400
    
    # Update stock
    new_quantity = prod_quantity - 1

    db.execute(
        "UPDATE products SET prod_quantity = ? WHERE prod_id = ?",
        (new_quantity, prod_id),
    )
    db.commit()
    
    return redirect(VIEWS["Summary"])  # Redirect back to the index page


@app.route('/add/<int:prod_id>/<string:type>', methods=['POST'])
def add(prod_id, type):
    """Increase stock quantity for a specific product."""
    db = get_db()

    # Fetch current stock
    current_stock = db.execute(
        "SELECT prod_quantity FROM products WHERE prod_id = ?", (prod_id,)
    ).fetchone()

    if not current_stock:
        return {"error": "Product not found"}, 404

    prod_quantity = current_stock["prod_quantity"]

    # Update stock
    new_quantity = prod_quantity + 1

    db.execute(
        "UPDATE products SET prod_quantity = ? WHERE prod_id = ?",
        (new_quantity, prod_id),
    )
    db.commit()
    
    return redirect(VIEWS["Summary"])  # Redirect back to the index page


@app.route("/edit", methods=["POST"])
def edit():
    edit_record_type = request.args.get("type")
    db = get_db()
    
    if edit_record_type == "location":
        loc_id = request.form.get("loc_id")
        loc_name = request.form.get("loc_name")
        if loc_name and loc_name.strip():
            try:
                db.execute("UPDATE locations SET loc_name = ? WHERE loc_id = ?", (loc_name, loc_id))
                db.commit()
            except sqlite3.IntegrityError:
                return {"error": "Location name must be unique"}, 400
            return redirect(VIEWS["Locations"])

    if edit_record_type == "product":
        prod_id = request.form.get("prod_id")
        prod_name = request.form.get("prod_name")
        prod_quantity = request.form.get("prod_quantity")
        loc_id = request.form.get("loc_id")

        if prod_name and prod_name.strip():
            db.execute("UPDATE products SET prod_name = ? WHERE prod_id = ?", (prod_name, prod_id))
        if prod_quantity:
            prod_quantity = int(prod_quantity)
            db.execute(
                "UPDATE products SET prod_quantity = ? WHERE prod_id = ?",
                (prod_quantity, prod_id),
            )
        if loc_id:
            # Validate loc_id against the locations table
            location_exists = db.execute(
                "SELECT loc_id FROM locations WHERE loc_id = ?", (loc_id,)
            ).fetchone()
            if not location_exists:
                return {"error": "Invalid location ID"}, 400

            db.execute("UPDATE products SET loc_id = ? WHERE prod_id = ?", (loc_id, prod_id))
        db.commit()
        return redirect(VIEWS["Stock"])
    
    return redirect(VIEWS["Summary"])


if __name__ == "__main__":
    with app.app_context():
        app.init_db()
    app.run(debug=True, port=8001)