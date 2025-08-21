from pprint import pp
from unittest import result
from langchain_core.tools import tool
from src.utils.helper import convert_list_context_source_to_str
from src.utils.logger import logger
from langchain_core.runnables import RunnableConfig
from langchain_experimental.utilities import PythonREPL
from langchain_community.tools import DuckDuckGoSearchRun
from src.utils.rcmsizetool import predict_size_public
import psycopg2
import re
from dotenv import load_dotenv
import os
import json
from flask import request
from urllib.parse import quote
duckduckgo_search = DuckDuckGoSearchRun()
python_exec = PythonREPL()
load_dotenv()
conn_str = os.getenv("SUPABASE_DB_URL")
conn = psycopg2.connect(conn_str)
cursor = conn.cursor()

# Hàm gợi ý size
def predict_size_model(user_height_text: str, user_weight_text: str,
                        user_gender_text: str, user_fit_text: str = "vừa") -> str:
    """
    Gợi ý size dựa vào chiều cao, cân nặng, giới tính, phong cách mặc (ôm, vừa, rộng)
    Args:
        height : chiều cao của người dùng
        weight: cân nặng của người dùng
        gender : giới tính của người dùng
        fit : phong cách như ôm, vừa, rộng
    """
    # Parse số “165cm”, “50kg” thành float
    import re
    h_match = re.search(r"(\d+(?:\.\d+)?)\s*cm", user_height_text.lower())
    w_match = re.search(r"(\d+(?:\.\d+)?)\s*kg", user_weight_text.lower())
    if not h_match or not w_match:
        return "Vui lòng cung cấp chiều cao (cm) và cân nặng (kg)."

    height = float(h_match.group(1))
    weight = float(w_match.group(1))

    base, final = predict_size_public(user_gender_text, height, weight, user_fit_text, True)
    return f"Size cơ bản: {base}. Theo phong cách '{user_fit_text}': {final}."


# Tìm kiếm sản phẩm dựa vào tiêu chí giá, size, màu.
def extract_query_product(
    size: str = "",
    color: str = "",
    price_range: str = "",
    in_stock: bool = True,
    limit: int = 5,
    country_code: str = "",
    lang: str = "",
    category_name: str = "",
) -> list:
    """
    Truy vấn sản phẩm theo kích cỡ, màu sắc, khoảng giá, còn hàng và giá theo quốc gia, danh mục sản phẩm là thời trang nam hoặc thời trang nữ.
    Args:
        size (str): Kích cỡ sản phẩm.
        color (str): Màu sắc sản phẩm.
        price_range (str): Khoảng giá sản phẩm. Nếu là tiếng việt (Việt Nam) thì truy vấn giá theo quốc gia Việt Nam, còn tiếng anh (Mỹ) thì truy vấn giá theo quốc gia Mỹ. Ví dụ nếu là tiếng việt thì có thể là " dưới 500k", "trên 500K", "khoảng 200k", "từ 200-500k", còn nếu tiếng anh thì có thể là "under 50$", "over 500$", "about 200$", "from 200-500$".
        in_stock (bool): Chỉ lấy sản phẩm còn hàng.
        limit (int): Số lượng sản phẩm trả về.
        country_code (str): Mã quốc gia để lấy giá theo quốc gia.
        lang (str): Ngôn ngữ của người dùng, ảnh hưởng đến cách hiển thị kết quả.
        category_name (str): Tên danh mục sản phẩm để lọc kết quả. Ví dụ: thời trang nam, thời trang nữ.
    Returns:
        list: Danh sách sản phẩm phù hợp dưới dạng markdown.
        Có kèm hình ảnh sản phẩm và link đến trang chi tiết sản phẩm.
    """
    if not country_code:
        country_code = "US" if lang == "en" else "VN"
    price_unit = "$" if lang == "en" else "VND"
    sql = """
    SELECT 
        p.id,
        p.name AS product_name,
        pp.price,
        v.size,
        v.color,
        v.sku,
        v.stock,
        p.images[1] AS image_url
    FROM "Product" p
    LEFT JOIN "ProductVariant" v ON v."productId" = p.id
    LEFT JOIN "ProductPrice" pp ON pp."productId" = p.id
    LEFT JOIN "Country" c ON c.id = pp."countryId"
    LEFT JOIN "Category" cat ON cat.id = p."categoryId"
    WHERE 1=1
    """
    params = []
    # ✅ Lọc quốc gia
    sql += " AND c.code = %s"
    params.append(country_code)
    # ✅ Lọc theo category name
    if category_name:
        sql += " AND cat.name ILIKE %s"
        params.append(f"%{category_name.strip()}%")
    # Lọc theo size
    if size:
        sql += " AND v.size ILIKE %s"
        params.append(f"%{size.strip()}%")
    # Lọc theo màu sắc
    if color:
        sql += " AND v.color ILIKE %s"
        params.append(f"%{color.strip()}%")
    # Lọc theo còn hàng
    if in_stock:
        sql += " AND v.stock > 0"
    # Lọc theo khoảng giá theo quốc gia
    price_min = 0
    price_max = 1e9
    if price_range:
        t = price_range.lower().replace(".", "").replace(",", "")
        t = t.replace("tr", "000000").replace("k", "000")  
        t = re.sub(r"[^\d\-]", " ", t)  
        digits = [int(s) for s in t.split() if s.isdigit()]
        if "dưới" in t and digits:
            price_max = digits[0]
        elif "trên" in t and digits:
            price_min = digits[0] * 4
        elif "khoảng" in t and len(digits) == 1:
            price_min = price_max = digits[0]
        elif "từ" in t and "-" in t:
            try:
                parts = t.split("-")
                price_min = int("".join(filter(str.isdigit, parts[0])))
                price_max = int("".join(filter(str.isdigit, parts[1])))
            except:
                pass
        elif "under" in t and digits:
            price_max = digits[0]
        elif "over" in t and digits:
            price_min = digits[0] * 4
        elif "about" in t and digits:
            price_min = price_max = digits[0]
            price_min *= 4
        elif "from" in t and "-" in t:
            try:
                parts = t.split("-")
                price_min = int("".join(filter(str.isdigit, parts[0])))
                price_max = int("".join(filter(str.isdigit, parts[1])))
            except:
                pass
        elif "-" in t:
            try:
                parts = t.split("-")
                price_min = int("".join(filter(str.isdigit, parts[0])))
                price_max = int("".join(filter(str.isdigit, parts[1])))
            except:
                pass
    sql += " AND pp.price BETWEEN %s AND %s"
    params.extend([price_min, price_max])
    sql += " ORDER BY pp.price ASC LIMIT %s"
    params.append(limit)
    cursor.execute(sql, params)
    products = cursor.fetchall()
    pp(products)
    if not products:
        return "😔 Không tìm thấy sản phẩm nào phù hợp với yêu cầu của bạn."
    response = "🔎 **Kết quả tìm kiếm sản phẩm:**\n"
    for p in products:
        pid, name, price, size, color, sku, stock, images_url = p
        price_fmt = f"{price:,.0f} {price_unit}"
        response += (
            f"\n🧥 **{name}**\n"
            f"- Danh mục: {category_name}\n"
            f"- 💰 Giá: {price_fmt}\n"
            f"- 🎨 Màu: {color} | 📏 Size: {size}\n"
            f"- 🔢 SKU: {sku} | 📦 Có sẵn: {stock}\n"
            f"- [Xem chi tiết](https://aifshop.vercel.app/products/{pid})\n"
            f"- 🖼️ Hình ảnh: ![Image]({images_url})\n"
        )
    response += "\n👉 Bạn muốn xem chi tiết sản phẩm nào không?"
    return response

# Trích xuất kiểm tra đơn hàng
def check_order_status(
    order_id: str = "", phone: str = "", country_code: str = "", lang: str = ""
) -> str:
    """
    Kiểm tra tình trạng đơn hàng và hiển thị chi tiết từng đơn hàng kèm thông tin khách hàng và sản phẩm theo số điện thoại hoặc mã đơn hàng.
    Args:
        order_id (str): Mã đơn hàng của đơn hàng.
        phone (str): Số điện thoại của khách hàng.
    Returns:
        str: Kết quả kiểm tra đơn hàng dưới dạng markdown.
    """
    if not country_code:
        country_code = "US" if lang == "en" else "VN"
    price_unit = "$" if lang == "en" else "VND"
    sql = """
        SELECT 
            o.id,
            o."orderCode",
            o.status,
            o."createdAt",
            o.total,
            o."shippingFullName",
            a."firstName",
            a."lastName",
            o."shippingEmail",
            a.phone,
            o."customerNote",
            a.street,
            a.ward,
            a.district,
            a.province,
            a."countryId",
            o."uniqueCode"
        FROM "Order" o
        LEFT JOIN "Address" a ON a.id = o."addressId"
        WHERE 1=1
    """
    params = []
    if order_id:
        sql += ' AND o."uniqueCode" ILIKE %s'
        params.append(f"%{order_id}%")
    if phone:
        sql += " AND a.phone ILIKE %s"
        params.append(f"%{phone}%")
    sql += ' ORDER BY o."createdAt" DESC LIMIT 3'
    try:
        cursor.execute(sql, params)
        orders = cursor.fetchall()
        if not orders:
            return "Không tìm thấy đơn hàng nào khớp với thông tin bạn cung cấp."
        response = "Tôi đã tìm thấy các đơn hàng của bạn với thông tin đã cung cấp:\n"
        for order in orders:
            (
                order_db_id,
                order_code,
                status,
                created_at,
                total,
                shipping_full_name,
                first_name,
                last_name,
                email,
                phone_num,
                note,
                street,
                ward,
                district,
                province,
                country_id,
                unique_code,
            ) = order
            full_name = shipping_full_name.strip() if shipping_full_name else f"{first_name or ''} {last_name or ''}".strip()
            created_at_fmt = created_at.strftime("%d/%m/%Y")
            total_fmt = f"{total:,.0f} {price_unit}"
            note = note if note else "(không có ghi chú)"
            shipping_address = ", ".join([p for p in [street, ward, district, province] if p])
            # Lấy sản phẩm (dùng cách lấy hình như extract_query_product)
            item_sql = """
                SELECT 
                    p.name,
                    v.size,
                    v.color,
                    i.quantity,
                    i.price,
                    COALESCE(p.images[1], '') AS image_url
                FROM "OrderItem" i
                JOIN "Product" p ON p.id = i."productId"
                JOIN "ProductVariant" v ON v.id = i."productVariantId"
                WHERE i."orderId" = %s
            """
            cursor.execute(item_sql, (order_db_id,))
            items = cursor.fetchall()
            response += f"\n**Đơn hàng #{unique_code}**\n"
            # Hiển thị sản phẩm trước, mỗi thuộc tính xuống dòng
            for name, size, color, quantity, price, image_url in items:
                price_fmt = f"{price:,.0f} {price_unit}"
                response += (
                    f"* Sản phẩm: {name}\n"
                    f"  - Size: {size}\n"
                    f"  - Màu: {color}\n"
                    f"  - Số lượng: {quantity}\n"
                    f"  - Giá: {price_fmt}\n"
                )
                if image_url:
                    response += f"  - 🖼️ Hình ảnh: ![Image]({image_url})\n"
            # Sau đó mới tới thông tin đơn hàng
            response += (
                f"- Trạng thái: {status}\n"
                f"- Ngày đặt: {created_at_fmt}\n"
                f"- Tổng tiền: {total_fmt}\n"
                f"- Người nhận: {full_name}\n"
                f"- Email: {email}\n"
                f"- Số điện thoại: {phone_num}\n"
                f"- Địa chỉ giao hàng: {shipping_address}\n"
                f"- Ghi chú: {note}\n"
                f"- Mã đơn hàng duy nhất: {unique_code}\n"
            )
        return response
    except Exception as e:
        logger.error(f"Error checking order: {e}")
        return "Đã xảy ra lỗi khi kiểm tra đơn hàng. Vui lòng thử lại sau."

# Truy vấn thông tin chi tiết sản phẩm
def extract_information_product(
    product_keyword: str, lang: str = "vi", country_code: str = ""
) -> str:
    """
    Truy vấn thông tin chi tiết sản phẩm theo từ khóa hoặc tên sản phẩm.
    Args:
        product_keyword (str): Từ khóa hoặc tên sản phẩm cần tìm.
        lang (str): Ngôn ngữ của người dùng, ảnh hưởng đến cách hiển thị kết quả.
        country_code (str): Mã quốc gia để lấy giá theo quốc gia.
    Returns:
        str: Kết quả thông tin chi tiết sản phẩm dưới dạng markdown.
    """
    if not country_code:
        country_code = "US" if lang == "en" else "VN"
    price_unit = "$" if lang == "en" else "VND"
    sql = """
        SELECT 
            p.id,
            p.name,
            p.description,
            pp.price AS country_price,
            p.stock,
            p.images[1] AS image_url,
            c.name AS category_name,
            v.id AS variant_id,
            v.color,
            v.size,
            v.stock AS variant_stock,
            v.sku,
            v.weight
        FROM "Product" p
        LEFT JOIN "ProductVariant" v ON v."productId" = p.id 
        LEFT JOIN "Category" c ON c.id = p."categoryId"
        LEFT JOIN "ProductPrice" pp ON pp."productId" = p.id
        LEFT JOIN "Country" co ON co.id = pp."countryId"
        WHERE LOWER(p.name) ILIKE %s AND co.code = %s
        ORDER BY v.size, v.color
    """
    cursor.execute(sql, (f"%{product_keyword.lower()}%", country_code))
    rows = cursor.fetchall()
    if not rows:
        return f"Không tìm thấy sản phẩm nào khớp với từ khóa: {product_keyword}"
    first = rows[0]
    name, desc, price, stock, images_url, category = (
        first[1],
        first[2],
        first[3],
        first[4],
        first[5],
        first[6],
    )
    response = f"🛍 **{name}**\n"
    response += (
        f"- Danh mục: {category}\n"
        f"- Giá: {price:,.0f} {price_unit}\n"
        f"- Có sẵn: {stock}\n"
        f"- Mô tả: {desc}\n"
        f"- [Xem chi tiết](https://aifshop.vercel.app/products/{first[0]})\n"
    )
    response += f"- 🖼️ Hình ảnh: ![Image]({images_url})\n"
    response += "\n🔄 **Các biến thể:**\n"
    for row in rows:
        response += (
            f"* Màu: {row[8]} \n"
            f"  Size: {row[9]} – \n"
            f"  SKU: {row[11]} – \n"
            f"  Có sẵn: {row[10]} – \n"
            f"  Nặng: {row[12]}kg\n"
        )
    return response


# Lấy thông tin về các chương trình khuyến mãi hiện có
def check_active_coupons(lang: str = "", country_code: str = "") -> str:
    """
    Trả về danh sách các mã giảm giá còn hiệu lực dưới dạng markdown.
    """
    if not country_code:
        country_code = "US" if lang == "en" else "VN"
    price_unit = "$" if lang == "en" else "VND"
   
    sql = """
        SELECT 
            code,
            type,
            "discountType",
            "discountValue",
            "maxDiscount",
            "minOrderValue",
            "startDate",
            "endDate"
        FROM "Coupon"
        WHERE "isActive" = TRUE
          AND NOW() BETWEEN "startDate" AND "endDate"
        ORDER BY "startDate" DESC
        LIMIT 10
    """
    try:
        cursor.execute(sql)
        coupons = cursor.fetchall()

        if not coupons:
            return "Hiện tại không có mã giảm giá nào đang hoạt động."

        response = "🎁 **Danh sách mã giảm giá hiện có:**\n"
        for c in coupons:
            (
                code,
                ctype,
                discount_type,
                discount_value,
                max_discount,
                min_order_value,
                start_date,
                end_date,
            ) = c

            # Hiển thị mức giảm
            if discount_type == "AMOUNT":
                discount_info = f"{discount_value:,.0f} {price_unit}"
            elif discount_type == "PERCENT":
                discount_info = f"{discount_value:.0f}%"
            else:
                discount_info = f"{discount_value}"

            max_discount_str = (
                f" – Giảm tối đa {max_discount:,.0f} {price_unit}"
                if max_discount
                else ""
            )
            min_order_str = (
                f" – Đơn tối thiểu {min_order_value:,.0f} {price_unit}"
                if min_order_value
                else ""
            )

            response += (
                f"- Mã **{code}**: Giảm {discount_info}{max_discount_str}{min_order_str}\n"
                f"  ⏳ Hiệu lực: {start_date.strftime('%d/%m/%Y')} → {end_date.strftime('%d/%m/%Y')}\n"
            )

        return response

    except Exception as e:
        logger.error(f"Lỗi khi lấy coupon: {e}")
        return "Đã xảy ra lỗi khi kiểm tra mã giảm giá. Vui lòng thử lại sau."
