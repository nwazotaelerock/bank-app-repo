import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response, jsonify
from flask_wtf.csrf import CSRFProtect
import firebase_admin
from firebase_admin import credentials, db, auth
from collections import defaultdict
import csv
from io import StringIO
from flask_wtf.csrf import validate_csrf
#test
# Initialize Flask
app = Flask(__name__)
app.secret_key = 'your-secure-dev-key-123'
csrf = CSRFProtect(app)

# Initialize Firebase
cred = credentials.Certificate("/home/elerock/Documents/biterite homepage/program/firebase/webapp2/credentials.json")
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://biterite-2fa73-default-rtdb.firebaseio.com/'
})

def validate_csrf(token):
    """Validate CSRF token using Flask-WTF's validator"""
    try:
        validate_csrf(token)
        return True
    except:
        return False



# ----------------- Context Processors -----------------
@app.context_processor
def inject_helpers():
    """Inject template helper functions and datetime"""
    def calculate_cart_total():
        cart = get_cart()
        products = get_firebase_data('products') or {}
        return sum(
            products.get(pid, {}).get('price', 0) * qty 
            for pid, qty in cart.items() 
            if pid in products
        )

    def get_cart_items():
        cart = get_cart()
        products = get_firebase_data('products') or {}
        items = []
        for pid, qty in cart.items():
            product = products.get(pid)
            if product and qty > 0:
                items.append({
                    'id': pid,
                    'name': product.get('name', '[Deleted Product]'),
                    'price': product.get('price', 0),
                    'quantity': qty,
                    'image': (product.get('images', [])[0] if product.get('images')
                             else url_for('static', filename='images/default.png'))
                })
        return items

    return dict(
        datetime=datetime,
        get_product_name=lambda products, pid: products.get(pid, {}).get('name', '[Deleted Product]'),
        get_cart=get_cart,
        calculate_cart_total=calculate_cart_total,
        get_cart_items=get_cart_items
    )

# ----------------- Template Filters -----------------
@app.template_filter('datetimeformat')
def datetimeformat(value, format="%b %d, %Y %I:%M %p"):
    """Safe datetime formatting filter"""
    try:
        if isinstance(value, str):
            return datetime.fromisoformat(value).strftime(format)
        return value.strftime(format)
    except:
        return "N/A"

# ----------------- Cart Helpers -----------------
def get_cart():
    """Get current cart from session"""
    return session.get('cart', {})

def update_cart(pid, quantity):
    """Update cart with proper session modification tracking"""
    cart = session.get('cart', {})
    
    if quantity > 0:
        cart[pid] = quantity
    else:
        cart.pop(pid, None)
    
    session['cart'] = cart
    session.modified = True
    return True

def clear_cart():
    """Empty the cart completely"""
    session.pop('cart', None)
    session.modified = True

def calculate_cart_total():
    """Calculate the total value of items in the cart"""
    try:
        cart = get_cart()
        products = get_firebase_data('products') or {}
        total = 0.0
        
        for pid, qty in cart.items():
            product = products.get(pid)
            if product and 'price' in product:
                total += product['price'] * qty
                
        return round(total, 2)
    
    except Exception as e:
        app.logger.error(f"Error calculating cart total: {str(e)}")
        return 0.0


# ----------------- Firebase Helpers -----------------
def get_firebase_data(path):
    """Safe data retrieval with error handling"""
    try:
        return db.reference(path).get() or {}
    except Exception as e:
        flash(f"Database error: {str(e)}", "danger")
        return {}

def update_firebase_data(path, data):
    """Safe data update with error handling"""
    try:
        db.reference(path).update(data)
        return True
    except Exception as e:
        flash(f"Update failed: {str(e)}", "danger")
        return False

def delete_firebase_data(path):
    """Safe deletion with error handling"""
    try:
        db.reference(path).delete()
        return True
    except Exception as e:
        flash(f"Deletion failed: {str(e)}", "danger")
        return False

# ----------------- Error Handlers -----------------
@app.errorhandler(500)
def internal_error(error):
    app.logger.error(f"500 Error: {str(error)}")
    flash("An internal server error occurred", "danger")
    return redirect(url_for('home'))

# ----------------- Application Routes -----------------
@app.route('/')
def home():
    """Main dashboard view"""
    products = get_firebase_data('products')
    total_value = sum(p.get('price', 0) * p.get('quantity', 0) for p in products.values())
    return render_template('home.html',
                         products=products,
                         total_value=total_value)

@app.route('/sales', methods=['GET', 'POST'])
def sales():
    """Sales processing and display"""
    if request.method == 'POST':
        try:
            selected_products = {
                pid: int(request.form.get(f'quantity_{pid}'))
                for pid in request.form.getlist('product_purchased')
                if pid and request.form.get(f'quantity_{pid}')
            }

            if not selected_products:
                flash("No products selected for sale!", "warning")
                return redirect(url_for('sales'))

            # Validate stock and calculate total
            valid_sale = True
            sale_total = 0
            products_ref = db.reference('products')
            
            for pid, qty in selected_products.items():
                product = products_ref.child(pid).get()
                if not product or product.get('quantity', 0) < qty:
                    flash(f"Invalid product selection: {pid}", "danger")
                    valid_sale = False
                    break
                sale_total += product.get('price', 0) * qty

            if not valid_sale:
                return redirect(url_for('sales'))

            # Record sale
            sale_data = {
                'timestamp': datetime.now().isoformat(),
                'products': selected_products,
                'total': sale_total,
                'payment_method': request.form.get('payment_method', 'cash'),
                'cashier': 'In-store'
            }
            db.reference('sales').push(sale_data)

            # Update inventory
            for pid, qty in selected_products.items():
                products_ref.child(pid).update({
                    'quantity': db.reference(f'products/{pid}/quantity').get() - qty
                })

            flash("Sale processed successfully!", "success")
        except Exception as e:
            flash(f"Sale processing error: {str(e)}", "danger")

    # Prepare sales data for display
    products = get_firebase_data('products')
    raw_sales = get_firebase_data('sales')
    processed_sales = {}

    for sale_id, sale in (raw_sales or {}).items():
        valid_products = {}
        for pid, qty in sale.get('products', {}).items():
            if pid in products:
                valid_products[pid] = qty
        if valid_products:
            processed_sales[sale_id] = sale
            processed_sales[sale_id]['products'] = valid_products

    return render_template('sales.html',
                         products=products,
                         sales=processed_sales)

@app.route('/store')
def store():
    """Online store front"""
    products = get_firebase_data('products') or {}
    return render_template('store.html', products=products)

@app.route('/cart', methods=['GET', 'POST'])
def cart():
    """Cart management endpoint"""
    if request.method == 'POST':
        try:
            # Validate CSRF token first
            csrf_token = request.form.get('csrf_token')
            if not csrf_token or not validate_csrf(csrf_token):
                return jsonify({
                    'success': False,
                    'message': 'Invalid CSRF token'
                }), 400

            # Validate required parameters
            action = request.form.get('action')
            pid = request.form.get('product_id')
            quantity = request.form.get('quantity', '1')

            if not all([action, pid]):
                return jsonify({
                    'success': False,
                    'message': 'Missing required parameters'
                }), 400

            # Get product data
            products = get_firebase_data('products') or {}
            product = products.get(pid)
            
            if not product:
                return jsonify({
                    'success': False,
                    'message': 'Product not found'
                }), 404

            # Get current cart
            cart = get_cart()
            current_qty = cart.get(pid, 0)

            # Process different actions
            if action == 'add':
                try:
                    quantity = int(quantity)
                    new_qty = current_qty + quantity
                except ValueError:
                    return jsonify({
                        'success': False,
                        'message': 'Invalid quantity format'
                    }), 400

                if product['quantity'] < new_qty:
                    return jsonify({
                        'success': False,
                        'message': f"Only {product['quantity']} available in stock"
                    }), 400

                cart[pid] = new_qty

            elif action == 'update':
                try:
                    new_qty = int(quantity)
                except ValueError:
                    return jsonify({
                        'success': False,
                        'message': 'Invalid quantity format'
                    }), 400

                if new_qty < 0:
                    return jsonify({
                        'success': False,
                        'message': 'Quantity cannot be negative'
                    }), 400

                if new_qty == 0:
                    cart.pop(pid, None)
                else:
                    if product['quantity'] < new_qty:
                        return jsonify({
                            'success': False,
                            'message': f"Only {product['quantity']} available in stock"
                        }), 400
                    cart[pid] = new_qty

            elif action == 'remove':
                cart.pop(pid, None)

            else:
                return jsonify({
                    'success': False,
                    'message': 'Invalid action'
                }), 400

            # Update session
            session['cart'] = cart
            session.modified = True

            return jsonify({
                'success': True,
                'cart_count': len(cart),
                'item_total': product['price'] * cart.get(pid, 0),
                'cart_total': calculate_cart_total()
            })

        except Exception as e:
            app.logger.error(f"Cart error: {str(e)}")
            return jsonify({
                'success': False,
                'message': 'Server error'
            }), 500

    # GET request - show cart page
    products = get_firebase_data('products') or {}
    return render_template('cart.html', products=products)



@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    """Final checkout and order processing"""
    cart_items = get_cart()
    if not cart_items:
        flash("Your cart is empty!", "warning")
        return redirect(url_for('store'))
    
    products = get_firebase_data('products') or {}
    
    # Validate stock
    valid = True
    for pid, qty in cart_items.items():
        product = products.get(pid)
        if not product or product.get('quantity', 0) < qty:
            flash(f"Sorry, {product['name'] if product else 'Item'} is no longer available in requested quantity", "warning")
            valid = False
    
    if not valid:
        return redirect(url_for('cart'))
    
    if request.method == 'POST':
        try:
            # Process customer details
            customer_data = {
                'name': request.form.get('name', '').strip(),
                'phone': request.form.get('phone', '').strip(),
                'address': request.form.get('address', '').strip(),
                'payment_method': 'online'
            }
            
            if not all(customer_data.values()):
                flash("All customer details are required!", "danger")
                return redirect(url_for('checkout'))
            
            # Create sale record
            sale_data = {
                'timestamp': datetime.now().isoformat(),
                'products': get_cart(),
                'total': sum(products[pid]['price'] * qty for pid, qty in cart_items.items()),
                'customer': customer_data,
                'payment_method': 'online',
                'cashier': 'Online Store'
            }
            
            # Update inventory
            products_ref = db.reference('products')
            for pid, qty in cart_items.items():
                products_ref.child(pid).update({
                    'quantity': db.reference(f'products/{pid}/quantity').get() - qty
                })
            
            # Save sale and get generated ID
            sale_ref = db.reference('sales').push(sale_data)
            sale_id = sale_ref.key
            clear_cart()
            
            flash("Order placed successfully! Thank you for shopping with us.", "success")
            return redirect(url_for('generate_receipt', sale_id=sale_id))
            
        except Exception as e:
            flash(f"Checkout failed: {str(e)}", "danger")
    
    return render_template('checkout.html')

# ----------------- Product Management Routes -----------------
@app.route('/add_product', methods=['GET', 'POST'])
def add_product():
    """Add new product"""
    if request.method == 'POST':
        try:
            product_data = {
                'name': request.form.get('name', '').strip(),
                'quantity': int(request.form.get('quantity', 0)),
                'price': float(request.form.get('price', 0)),
                'images': [url.strip() for url in request.form.getlist('image_urls[]') if url.strip()][:5]
            }
            
            if not product_data['name']:
                flash("Product name is required!", "danger")
                return redirect(url_for('add_product'))
                
            if db.reference('products').push(product_data):
                flash("Product added successfully!", "success")
                return redirect(url_for('home'))
                
        except ValueError:
            flash("Invalid numeric values in form!", "danger")
        except Exception as e:
            flash(f"Error adding product: {str(e)}", "danger")
    
    return render_template('add_product.html')

@app.route('/update/<string:product_id>', methods=['GET', 'POST'])
def update_product(product_id):
    """Update product quantity"""
    product = get_firebase_data(f'products/{product_id}')
    if not product:
        flash("Product not found!", "danger")
        return redirect(url_for('home'))

    if request.method == 'POST':
        try:
            new_quantity = int(request.form.get('quantity', 0))
            if update_firebase_data(f'products/{product_id}', {'quantity': new_quantity}):
                flash("Quantity updated successfully!", "success")
            return redirect(url_for('home'))
        except ValueError:
            flash("Invalid quantity value!", "danger")
    
    return render_template('update_product.html', product=product)

@app.route('/delete/<string:product_id>', methods=['GET', 'POST'])
def delete_product(product_id):
    """Delete product"""
    product = get_firebase_data(f'products/{product_id}')
    if not product:
        flash("Product not found!", "danger")
        return redirect(url_for('home'))

    if request.method == 'POST':
        if delete_firebase_data(f'products/{product_id}'):
            flash("Product deleted successfully!", "success")
        return redirect(url_for('home'))
    
    return render_template('delete_product.html', product=product)

@app.route('/delete_zero_stock', methods=['POST'])
def delete_zero_stock():
    """Clear out-of-stock items"""
    try:
        products = get_firebase_data('products')
        deleted_count = 0
        
        for pid in list(products.keys()):
            if products[pid].get('quantity', 0) <= 0:
                if delete_firebase_data(f'products/{pid}'):
                    deleted_count += 1
        
        flash(f"Cleared {deleted_count} out-of-stock items!", "success")
    except Exception as e:
        flash(f"Error clearing stock: {str(e)}", "danger")
    
    return redirect(url_for('home'))

# ----------------- Reporting Routes -----------------
@app.route('/generate_receipt/<string:sale_id>')
def generate_receipt(sale_id):
    try:
        sale = get_firebase_data(f'sales/{sale_id}')
        if not sale:
            flash("Sale record not found", "danger")
            return redirect(url_for('sales'))

        products = get_firebase_data('products') or {}
        receipt_items = []
        sale_products = sale.get('products', {})

        for pid, qty in sale_products.items():
            product = products.get(str(pid), {'name': f'[Deleted Product {pid}]', 'price': 0})
            unit_price = float(product.get('price', 0))
            receipt_items.append({
                'id': pid,
                'name': product['name'],
                'quantity': qty,
                'unit_price': unit_price,
                'total_price': unit_price * qty
            })

        receipt_data = {
            'sale_id': sale_id,
            'timestamp': sale.get('timestamp', datetime.now().isoformat()),
            'items': receipt_items,
            'calculated_total': sum(item['total_price'] for item in receipt_items),
            'original_total': float(sale.get('total', 0)),
            'cashier': sale.get('cashier', 'System'),
            'payment_method': sale.get('payment_method', 'Cash'),
            'customer': sale.get('customer', {})
        }

        return render_template('receipt.html', receipt=receipt_data)

    except Exception as e:
        print(f"Error generating receipt: {str(e)}")
        flash(f"Receipt generation failed: {str(e)}", "danger")
        return redirect(url_for('sales'))

@app.route('/sales_report', methods=['GET', 'POST'])
def sales_report():
    try:
        # Initialize variables
        start_date_str = end_date_str = None
        analysis_data = {}
        filtered_sales = {}
        products = get_firebase_data('products') or {}
        all_sales = get_firebase_data('sales') or {}
        date_warning = False

        if request.method == 'POST':
            # Get and validate dates
            start_date_str = request.form.get('start_date')
            end_date_str = request.form.get('end_date')

            if not start_date_str or not end_date_str:
                flash("Please select both start and end dates", "warning")
                return redirect(url_for('sales_report'))

            try:
                # Parse dates
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

                # Auto-correct date order
                if start_date > end_date:
                    start_date, end_date = end_date, start_date
                    start_date_str, end_date_str = end_date_str, start_date_str
                    date_warning = True

                # Filter sales within date range
                filtered_sales = {
                    sale_id: sale for sale_id, sale in all_sales.items()
                    if start_date <= datetime.fromisoformat(sale['timestamp']).date() <= end_date
                }

                # Store filtered sales in session for export
                session['filtered_sales'] = filtered_sales

                # Initialize analysis data
                analysis_data = {
                    'total_sales': len(filtered_sales),
                    'total_revenue': 0.0,
                    'products_sold': defaultdict(int),
                    'payment_methods': defaultdict(int),
                    'hourly_sales': defaultdict(float),
                    'daily_product_sales': defaultdict(lambda: defaultdict(int))
                }

                # Process sales data
                for sale_id, sale in filtered_sales.items():
                    # Convert timestamp
                    sale_time = datetime.fromisoformat(sale['timestamp'])
                    date_key = sale_time.strftime('%Y-%m-%d')
                    hour_key = sale_time.strftime('%Y-%m-%d_%H')

                    # Update financial totals
                    sale_total = float(sale.get('total', 0))
                    analysis_data['total_revenue'] += sale_total
                    analysis_data['hourly_sales'][hour_key] += sale_total

                    # Track payment methods
                    payment_method = sale.get('payment_method', 'unknown').lower()
                    analysis_data['payment_methods'][payment_method] += 1

                    # Process products
                    for pid, qty in sale.get('products', {}).items():
                        product = products.get(pid, {'name': f'Deleted Product ({pid})'})
                        
                        # Update product sales
                        product_name = product['name']
                        analysis_data['products_sold'][product_name] += qty
                        
                        # Update daily product tracking
                        analysis_data['daily_product_sales'][date_key][product_name] += qty

                if date_warning:
                    flash("Date range was auto-corrected to chronological order", "warning")

            except ValueError as e:
                app.logger.error(f"Date parsing error: {str(e)}")
                flash("Invalid date format. Please use YYYY-MM-DD", "danger")
                return redirect(url_for('sales_report'))

        return render_template('sales_report.html',
                            start_date=start_date_str,
                            end_date=end_date_str,
                            sales=filtered_sales,
                            analysis=analysis_data,
                            products=products,
                            daily_product_sales=analysis_data.get('daily_product_sales', {}))

    except Exception as e:
        app.logger.error(f"Critical error in sales report: {str(e)}")
        flash("A system error occurred while generating the report", "danger")
        return redirect(url_for('home'))

@app.route('/export_report')
def export_report():
    try:
        # Get filtered sales from session
        filtered_sales = session.get('filtered_sales', {})
        products = get_firebase_data('products') or {}
        
        # Create CSV output
        output = StringIO()
        writer = csv.writer(output)
        
        # CSV Header
        writer.writerow([
            'Sale ID', 'Date', 'Time', 'Product ID', 'Product Name',
            'Quantity', 'Unit Price', 'Total', 'Payment Method', 'Customer Name', 'Phone'
        ])
        
        # CSV Rows
        for sale_id, sale in filtered_sales.items():
            sale_time = datetime.fromisoformat(sale['timestamp'])
            for pid, qty in sale.get('products', {}).items():
                product = products.get(pid, {'name': 'Deleted Product', 'price': 0})
                customer = sale.get('customer', {})
                writer.writerow([
                    sale_id,
                    sale_time.strftime('%Y-%m-%d'),
                    sale_time.strftime('%H:%M'),
                    pid,
                    product['name'],
                    qty,
                    product.get('price', 0),
                    qty * product.get('price', 0),
                    sale.get('payment_method', 'cash'),
                    customer.get('name', ''),
                    customer.get('phone', '')
                ])
        
        # Prepare response
        output.seek(0)
        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-disposition": "attachment; filename=sales_report.csv"}
        )
        
    except Exception as e:
        app.logger.error(f"Export error: {str(e)}")
        flash("Failed to generate export", "danger")
        return redirect(url_for('sales_report'))

# ----------------- Authentication Routes -----------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login"""
    if request.method == 'POST':
        try:
            # In production: Implement proper auth with Firebase Client SDK
            user = 'test' #auth.get_user_by_email(request.form.get('email', ''))
            session['user_id'] = user#.uid
            flash("Login successful!", "success")
            return redirect(url_for('home'))
        except: #auth.AuthError:
            flash("Invalid credentials!", "danger")
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """User logout"""
    session.pop('user_id', None)
    flash("You have been logged out", "success")
    return redirect(url_for('login'))

@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password_request():
    if request.method == 'POST':
        email = request.form.get('email')
        try:
            auth.generate_password_reset_link(email)
            flash('Password reset link sent to your email', 'success')
            return redirect(url_for('login'))
        except auth.AuthError:
            flash('Error sending reset link', 'danger')
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
    return render_template('reset_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if request.method == 'POST':
        new_password = request.form.get('password')
        try:
            auth.verify_password_reset_link(token)
            # Update password logic here (need to implement)
            flash('Password reset successfully!', 'success')
            return redirect(url_for('login'))
        except auth.AuthError:
            flash('Invalid or expired token', 'danger')
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
    return render_template('reset_password_confirm.html')


if __name__ == '__main__':
    app.run(port=5000, debug=True)
