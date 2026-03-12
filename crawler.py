import urllib.request
import urllib.parse
import re
import os
import time

# --- 配置区 ---
BASE_URL = "https://teronlighting.com/products/"
SAVE_DIR = "data/teron_downloads"
# 伪装成 Chrome 浏览器的 User-Agent
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

def download_file(url, folder):
    """下载单个文件并保存"""
    if not url:
        return
    
    # 处理相对路径
    if url.startswith('//'):
        url = 'https:' + url
    elif url.startswith('/'):
        url = 'https://teronlighting.com' + url

    try:
        filename = os.path.basename(url).split('?')[0] # 去除 URL 参数
        if not filename:
            return
            
        filepath = os.path.join(folder, filename)
        if os.path.exists(filepath):
            return # 跳过已存在的文件

        print(f"正在下载: {filename}")
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as response, open(filepath, 'wb') as out_file:
            out_file.write(response.read())
        
        # 适当休眠防止被封 IP
        time.sleep(0.5) 
    except Exception as e:
        print(f"下载失败 {url}: {e}")

def get_html_content(url):
    """获取页面 HTML 内容"""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req) as response:
            return response.read().decode('utf-8')
    except Exception as e:
        print(f"无法访问页面 {url}: {e}")
        return ""

def main():
    # 创建保存目录
    if not os.path.exists(SAVE_DIR):
        os.makedirs(SAVE_DIR)

    print(f"开始扫描目录页: {BASE_URL}")
    html = get_html_content(BASE_URL)
    
    # 1. 查找所有产品详情页链接 (假设链接格式包含 /product/)
    # 正则解释：匹配 href 中包含 /products/ 后接具体产品名的部分
    product_links = list(set(re.findall(r'href="(https://teronlighting.com/products/[^"]+/)"', html)))
    
    print(f"找到 {len(product_links)} 个产品页面。")

    for p_link in product_links:
        print(f"\n正在处理产品: {p_link}")
        p_html = get_html_content(p_link)
        
        # 2. 提取图片链接 (通常在 wp-content/uploads 目录下)
        # 匹配 jpg, png, webp 等
        images = re.findall(r'src="(https://teronlighting.com/wp-content/uploads/[^"]+\.(?:jpg|jpeg|png|webp))"', p_html)
        
        # 3. 提取 PDF 规格书链接
        pdfs = re.findall(r'href="([^"]+\.pdf)"', p_html)

        # 执行下载
        for img_url in set(images):
            download_file(img_url, SAVE_DIR)
            
        for pdf_url in set(pdfs):
            download_file(pdf_url, SAVE_DIR)

if __name__ == "__main__":
    main()