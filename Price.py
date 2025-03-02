
import os
import re
import logging
import time
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from pymongo import MongoClient, errors
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
from urllib.parse import urlparse
import streamlit as st
import threading

# Load environment variables
load_dotenv()

# 🔹 Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("price_tracker.log"), logging.StreamHandler()]
)

# 🔹 Database Configuration
class DatabaseManager:
    def __init__(self):
        self.client = None
        self.db = None
        self.connect()

    def connect(self):
        try:
            # Use the MongoDB Atlas connection string from the .env file
            self.client = MongoClient("mongodb+srv://tagemo5926:B0vogxwZjX0cyOcK@cluster0.yfstd.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
            self.db = self.client[os.getenv("DB_NAME", "AmazonFlipkartPriceTracker")]
            logging.info("✅ Successfully connected to MongoDB Atlas")
        except errors.ConnectionFailure as e:
            logging.error(f"❌ MongoDB Atlas connection failed: {e}")
            raise

    def get_collection(self, name="Products"):
        return self.db[name]

# 🔹 Base Product Parser
class BaseProductParser:
    PLATFORM = "Generic"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    def __init__(self, url):
        self.url = url
        self.soup = None
        self.product_data = {
            "name": "Unknown Product",
            "price": 0.0,
            "platform": self.PLATFORM,
            "url": url,
            "last_checked": datetime.now()
        }

    def fetch_page(self):
        try:
            response = requests.get(self.url, headers=self.HEADERS, timeout=10)
            response.raise_for_status()
            self.soup = BeautifulSoup(response.content, "html.parser")
            return True
        except Exception as e:
            logging.error(f"Error fetching page: {e}")
            return False

    def parse_price(self):
        raise NotImplementedError

    def parse_name(self):
        raise NotImplementedError

    def get_product_details(self):
        if self.fetch_page():
            self.parse_name()
            self.parse_price()
        return self.product_data

# 🔹 Flipkart Product Parser
class FlipkartParser(BaseProductParser):
    PLATFORM = "Flipkart"

    def parse_name(self):
        try:
            possible_name_tags = [
                "span.VU-ZEz",  # Primary name tag
                "h1._6EBuvT span",  # Alternative structure
                "h1",  # Fallback option,
            ]
            for selector in possible_name_tags:
                name_tag = self.soup.select_one(selector)
                if name_tag:
                    self.product_data["name"] = name_tag.get_text(strip=True)
                    return
            logging.error("Product name not found, Flipkart may have changed HTML structure.")
        except Exception as e:
            logging.error(f"Error parsing name: {e}")

    def parse_price(self):
        try:
            possible_price_tags = [
                "div.Nx9bqj",  # Primary price container
                "div._30jeq3._16Jk6d",  # Alternative class name
                "span._30jeq3",  # Fallback
            ]
            for selector in possible_price_tags:
                price_tag = self.soup.select_one(selector)
                if price_tag:
                    price_str = price_tag.get_text(strip=True).replace("₹", "").replace(",", "")
                    price = re.search(r"\d+(\.\d+)?", price_str)
                    self.product_data["price"] = round(float(price.group()), 2) if price else 0.0
                    return
            logging.error("Price not found, Flipkart may have changed HTML structure.")
        except Exception as e:
            logging.error(f"Error parsing price: {e}")

# 🔹 Amazon Product Parser
class AmazonParser(BaseProductParser):
    PLATFORM = "Amazon"

    def parse_name(self):
        try:
            name_tag = self.soup.find("span", id="productTitle")
            self.product_data["name"] = name_tag.get_text(strip=True) if name_tag else "Unknown Product"
        except Exception as e:
            logging.error(f"Error parsing name: {e}")

    def parse_price(self):
        try:
            price_str = None
            
            # Method 1: Combined whole and fraction
            whole = self.soup.find("span", class_="a-price-whole")
            fraction = self.soup.find("span", class_="a-price-fraction")
            if whole:
                price_str = whole.get_text(strip=True).replace(",", "")
                if fraction:
                    price_str += f".{fraction.get_text(strip=True)}"

            # Method 2: Offscreen price
            if not price_str:
                price_tag = self.soup.find("span", class_="a-offscreen")
                if price_tag:
                    price_str = price_tag.get_text(strip=True).replace(",", "")

            # Convert price to float and round to 2 decimal places
            if price_str:
                price = re.search(r"\d+(\.\d+)?", price_str)
                self.product_data["price"] = round(float(price.group()), 2) if price else 0.0
        except Exception as e:
            logging.error(f"Error parsing price: {e}")

# 🔹 Email Manager
class EmailManager:
    def __init__(self):
        self.sender = os.getenv("EMAIL_SENDER", "rnithin691@gmail.com")
        self.password = os.getenv("EMAIL_APP_PASSWORD", "apvnnfurzihocefq")  # Use App Password, not regular password
        self.receiver = os.getenv("EMAIL_RECEIVER", "nithinreddycheerapureddy@gmail.com")

    def send_alert(self, product, old_price):
        try:
            msg = MIMEMultipart()
            price_change = "increased" if product['price'] > old_price else "decreased"
            msg["Subject"] = f"📉 Price Alert: {product['name']} ({price_change})"
            msg["From"] = self.sender
            msg["To"] = self.receiver

            html = f"""
            <html>
                <body>
                    <h2>{product['name']}</h2>
                    <p>Price {price_change} on {product['platform']}:</p>
                    <p style="color: red; font-size: 24px;">
                        <del>₹{old_price}</del> → <strong>₹{product['price']}</strong>
                    </p>
                    <p><a href="{product['url']}">View Product</a></p>
                </body>
            </html>
            """
            msg.attach(MIMEText(html, "html"))
            
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.sender, self.password)
                server.sendmail(self.sender, self.receiver, msg.as_string())
            
            logging.info(f"📩 Email alert sent for {product['name']}")
        except Exception as e:
            logging.error(f"Failed to send email: {e}")

    def send_no_change_alert(self):
        try:
            msg = MIMEMultipart()
            msg["Subject"] = "📊 Price Tracker: No Price Changes"
            msg["From"] = self.sender
            msg["To"] = self.receiver

            html = """
            <html>
                <body>
                    <h2>Price Tracker Update</h2>
                    <p>All product prices remain the same.</p>
                </body>
            </html>
            """
            msg.attach(MIMEText(html, "html"))
            
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.sender, self.password)
                server.sendmail(self.sender, self.receiver, msg.as_string())
            
            logging.info("📩 Sent email: No price changes")
        except Exception as e:
            logging.error(f"Failed to send email: {e}")

# 🔹 Price Monitor
class PriceMonitor:
    def __init__(self):
        self.db = DatabaseManager()
        self.collection = self.db.get_collection()
        self.email_manager = EmailManager()

    def validate_url(self, url):
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except:
            return False

    def add_product(self, url):
        if not self.validate_url(url):
            logging.error("❌ Invalid URL format")
            return False

        parser = self.get_parser(url)
        product = parser.get_product_details()
        
        try:
            existing = self.collection.find_one({"url": url})
            if existing:
                logging.info(f"🔄 Product exists: {product['name']}")
                return False
            
            self.collection.insert_one(product)
            logging.info(f"✅ New product added: {product['name']}")
            return True
        except errors.PyMongoError as e:
            logging.error(f"Database error: {e}")
            return False

    def get_parser(self, url):
        domain = urlparse(url).netloc.lower()
        if "amazon" in domain:
            return AmazonParser(url)
        elif "flipkart" in domain:
            return FlipkartParser(url)
        return BaseProductParser(url)

    def check_price_changes(self):
        try:
            products = self.collection.find()
            price_changed = False

            for product in products:
                parser = self.get_parser(product['url'])
                new_product_data = parser.get_product_details()

                # Skip if the new price is ₹0.0 (temporarily unavailable)
                if new_product_data['price'] == 0.0:
                    logging.info(f"🔄 Price temporarily unavailable for {product['name']}")
                    continue

                # Skip if the product was just added (old price is 0.0)
                if product['price'] == 0.0:
                    self.collection.update_one(
                        {"_id": product['_id']},
                        {"$set": {"price": new_product_data['price'], "last_checked": datetime.now()}}
                    )
                    logging.info(f"🔄 Updated initial price for {product['name']}")
                    continue

                # Normalize prices to 2 decimal places
                old_price = round(float(product['price']), 2)
                new_price = round(float(new_product_data['price']), 2)

                # Debug logs for price comparison
                logging.info(f"🔄 Comparing prices for {product['name']}: Old Price = ₹{old_price}, New Price = ₹{new_price}")

                # Check for price changes
                if new_price > old_price:
                    # Price increased
                    self.email_manager.send_alert(new_product_data, old_price)
                    self.collection.update_one(
                        {"_id": product['_id']},
                        {"$set": {"price": new_price, "last_checked": datetime.now()}}
                    )
                    logging.info(f"🔼 Price increased for {product['name']}")
                    price_changed = True
                elif new_price < old_price:
                    # Price decreased
                    self.email_manager.send_alert(new_product_data, old_price)
                    self.collection.update_one(
                        {"_id": product['_id']},
                        {"$set": {"price": new_price, "last_checked": datetime.now()}}
                    )
                    logging.info(f"🔽 Price decreased for {product['name']}")
                    price_changed = True
                else:
                    # No price change
                    logging.info(f"🔄 No price change for {product['name']}")
                    continue  # Skip sending any email for no change

            # Send a single email if no price changes
            if not price_changed:
                self.email_manager.send_no_change_alert()
        except Exception as e:
            logging.error(f"Error checking price changes: {e}")

# 🔹 Background Thread for Price Monitoring
def start_price_monitoring(monitor):
    while True:
        monitor.check_price_changes()
        logging.info("⏳ Next check in 10 minutes...")
        time.sleep(10)  # Check every 10 minutes

# 🔹 Streamlit App
def main():
    st.title("📉 Price Tracker Dashboard")
    monitor = PriceMonitor()

    # Add a product
    st.write("### Add a Product to Track")
    product_url = st.text_input("Enter Amazon or Flipkart product URL:")
    if st.button("Add Product"):
        if monitor.add_product(product_url):
            st.success(f"✅ Product added successfully!")
        else:
            st.error("❌ Failed to add product. Please check the URL.")

    # Display tracked products
    st.write("### Tracked Products")
    products = list(monitor.collection.find())
    if products:
        for product in products:
            st.write(f"**Name**: {product['name']}")
            st.write(f"**Price**: ₹{product['price']}")
            st.write(f"**Platform**: {product['platform']}")
            st.write(f"**Last Checked**: {product['last_checked']}")
            st.write(f"**URL**: [View Product]({product['url']})")
            st.write("---")
    else:
        st.write("No products found in the database.")

# Run the Streamlit app
if __name__ == "__main__":
    # Start the price monitoring thread
    monitor = PriceMonitor()
    monitor_thread = threading.Thread(target=start_price_monitoring, args=(monitor,), daemon=True)
    monitor_thread.start()

    # Run the Streamlit app
    main()
