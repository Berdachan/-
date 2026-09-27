"""
OCR图像处理模块（优化版）
负责：图像预处理、文字识别、PDF转图片、大文件优化
"""

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract
from pathlib import Path
from typing import Optional
import logging
import gc

from config import (
    TESSERACT_CMD, SUPPORTED_IMAGE_FORMATS,
    OCR_MAX_PAGES, OCR_DEFAULT_DPI, OCR_MAX_DPI
)

logger = logging.getLogger(__name__)


class OCRProcessor:
    """OCR处理器：图像预处理 + 文字识别 + 大文件优化"""

    def __init__(self, lang: str = 'chi_sim+eng'):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
        self.lang = lang
        logger.info(f"OCR处理器初始化完成，语言: {lang}")

    def _adaptive_dpi(self, page_count: int) -> int:
        """根据页数自适应调整DPI"""
        if page_count > 30:
            dpi = 150
        elif page_count > 10:
            dpi = 200
        else:
            dpi = OCR_DEFAULT_DPI
        return min(dpi, OCR_MAX_DPI)

    # ========== 图像预处理 ==========

    def preprocess_image(self, image_path: Path) -> Image.Image:
        """图像预处理流水线：灰度化 → 增强对比度 → 二值化 → 去噪"""
        img = Image.open(image_path)
        logger.info(f"预处理图片: {image_path.name}, 原始尺寸: {img.size}")

        img = ImageOps.grayscale(img)
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(2.0)

        # 自适应阈值
        histogram = img.histogram()
        total_pixels = sum(histogram)
        sum_pixels = 0
        threshold = 0
        for i, count in enumerate(histogram):
            sum_pixels += i * count
            if sum_pixels >= total_pixels * 0.5:
                threshold = i
                break

        lut = [0 if i < threshold else 255 for i in range(256)]
        img = img.point(lut, '1')
        img = img.convert('L')
        img = img.filter(ImageFilter.MedianFilter(size=3))

        logger.info(f"预处理完成，输出尺寸: {img.size}")
        return img

    def preprocess_and_save(self, image_path: Path, output_dir: Optional[Path] = None) -> Path:
        """预处理图片并保存（调试用）"""
        img = self.preprocess_image(image_path)
        if output_dir is None:
            output_dir = image_path.parent / "preprocessed"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"pre_{image_path.name}"
        img.save(output_path)
        logger.info(f"预处理图片已保存: {output_path}")
        return output_path

    # ========== OCR识别 ==========

    def recognize(self, image_path: Path, preprocess: bool = True) -> str:
        """单张图片OCR识别"""
        if preprocess:
            img = self.preprocess_image(image_path)
        else:
            img = Image.open(image_path)

        custom_config = r'--psm 6 --oem 3'
        text = pytesseract.image_to_string(img, lang=self.lang, config=custom_config)

        # 合并短行
        lines = text.split('\n')
        merged_lines = []
        i = 0
        while i < len(lines):
            current = lines[i].strip()
            if len(current) < 20 and i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if len(next_line) < 20 and not next_line.startswith(
                    ('1.', '2.', '3.', '4.', '5.', '6.', '7.', '8.', '9.', '一', '二', '三', '四', '五')
                ):
                    merged_lines.append(current + ' ' + next_line)
                    i += 2
                    continue
            merged_lines.append(current)
            i += 1

        text = '\n'.join(merged_lines)
        logger.info(f"OCR识别完成: {image_path.name}, 文本长度: {len(text)}")
        return text

    def recognize_with_confidence(self, image_path: Path, preprocess: bool = True) -> list[dict]:
        """带置信度的OCR识别"""
        if preprocess:
            img = self.preprocess_image(image_path)
        else:
            img = Image.open(image_path)

        custom_config = r'--psm 6 --oem 3'
        data = pytesseract.image_to_data(
            img, lang=self.lang, config=custom_config,
            output_type=pytesseract.Output.DICT
        )

        results = []
        n_boxes = len(data['text'])
        for i in range(n_boxes):
            if int(data['conf'][i]) > 30:
                results.append({
                    'text': data['text'][i],
                    'conf': data['conf'][i],
                    'x': data['left'][i],
                    'y': data['top'][i],
                    'width': data['width'][i],
                    'height': data['height'][i]
                })
        return results

    # ========== PDF处理（优化版）==========

    def process_pdf(self, pdf_path: Path, dpi: Optional[int] = None) -> list[str]:
        """
        PDF多页识别（优化版：限制页数、自适应DPI、内存优化）
        """
        import fitz

        logger.info(f"开始处理PDF: {pdf_path.name}")
        doc = fitz.open(pdf_path)
        total_pages = len(doc)

        # 限制页数
        if total_pages > OCR_MAX_PAGES:
            logger.warning(f"PDF页数({total_pages})超过限制({OCR_MAX_PAGES})，只处理前{OCR_MAX_PAGES}页")
            pages_to_process = OCR_MAX_PAGES
        else:
            pages_to_process = total_pages

        # 自适应DPI
        if dpi is None:
            dpi = self._adaptive_dpi(total_pages)
        logger.info(f"使用DPI: {dpi}")

        results = []
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)

        for page_num in range(pages_to_process):
            try:
                page = doc[page_num]
                pix = page.get_pixmap(matrix=mat)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

                text = pytesseract.image_to_string(img, lang=self.lang)
                results.append(text)

                # 及时释放内存
                del pix, img
                if page_num % 5 == 0:
                    gc.collect()

                logger.info(f"第 {page_num+1}/{pages_to_process} 页识别完成")

            except Exception as e:
                logger.error(f"第 {page_num+1} 页处理失败: {e}")
                results.append(f"[第{page_num+1}页识别失败]")

        doc.close()
        gc.collect()
        logger.info(f"PDF处理完成，共 {len(results)} 页")
        return results

    # ========== Word文档处理 ==========

    def process_docx(self, docx_path: Path) -> str:
        """处理 Word 文档"""
        from docx import Document

        # 检查文件头，确保是真正的 docx 格式
        with open(docx_path, 'rb') as f:
            header = f.read(4)
        
        # .doc 二进制格式（OLE）
        if header == b'\xd0\xcf\x11\xe0':
            raise ValueError(
                f"文件 '{docx_path.name}' 实际是 .doc 格式（旧版Word），不是 .docx 格式。\n"
                f"请用 Microsoft Word 打开该文件，然后：\n"
                f"  文件 → 另存为 → 保存类型选择 'Word 文档 (*.docx)' → 保存\n"
                f"然后再导入新保存的 .docx 文件。"
            )
        
        # 检查扩展名
        if docx_path.suffix.lower() == ".doc":
            raise ValueError(
                "不支持 .doc 格式。请将文件另存为 .docx 格式后再导入。\n"
                "操作：用 Word 打开 → 文件 → 另存为 → 选择 .docx"
            )

        logger.info(f"开始处理Word文档: {docx_path.name}")
        doc = Document(str(docx_path))

        lines = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                lines.append(text)

        for table in doc.tables:
            for row in table.rows:
                row_text = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if row_text:
                    lines.append(" | ".join(row_text))

        result = "\n".join(lines)
        logger.info(f"Word文档处理完成，共 {len(lines)} 段")
        return result

    def process_doc(self, doc_path: Path) -> str:
        """处理 .doc 文件（旧版Word二进制格式）：先转 .docx 再读取"""
        import tempfile
        import shutil
        
        # 检查文件头，确认是 .doc 格式
        with open(doc_path, 'rb') as f:
            header = f.read(4)
        if header != b'\xd0\xcf\x11\xe0':
            raise ValueError(
                f"文件 '{doc_path.name}' 不是有效的 .doc 格式。"
            )
        
        # 尝试用 pywin32 转换
        try:
            import win32com.client as win32
        except ImportError:
            raise ImportError(
                "处理 .doc 文件需要 pywin32 库，且系统必须安装 Microsoft Word。\n"
                "请运行: pip install pywin32\n"
                "并确保已安装 Microsoft Word。"
            )
        
        # 复制到临时目录（纯英文路径，避免中文路径问题）
        temp_dir = Path(tempfile.gettempdir())
        temp_doc = temp_dir / f"temp_doc_{doc_path.stem}.doc"
        temp_docx = temp_dir / f"temp_docx_{doc_path.stem}.docx"
        
        try:
            shutil.copy2(str(doc_path), str(temp_doc))
        except Exception as e:
            raise RuntimeError(f"复制文件到临时目录失败: {e}")
        
        try:
            word = win32.Dispatch("Word.Application")
            word.Visible = False
            word.DisplayAlerts = 0  # 不显示警告
            
            doc = word.Documents.Open(str(temp_doc.absolute()))
            doc.SaveAs2(str(temp_docx.absolute()), FileFormat=16)  # 16 = wdFormatDocumentDefault (docx)
            doc.Close()
            word.Quit()
            
            # 用现有的 process_docx 读取转换后的文件
            text = self.process_docx(temp_docx)
            return text
            
        except Exception as e:
            # 确保 Word 进程被关闭
            try:
                word.Quit()
            except Exception:
                pass
            raise RuntimeError(f"转换 .doc 文件失败: {e}")
        finally:
            # 清理临时文件
            for f in [temp_doc, temp_docx]:
                if f.exists():
                    try:
                        f.unlink()
                    except Exception:
                        pass

    # ========== 批量处理 ==========

    def batch_process(self, input_dir: Path, preprocess: bool = True) -> dict[str, str]:
        """批量处理文件夹内所有支持的文件"""
        results = {}

        for file_path in input_dir.iterdir():
            if file_path.is_file():
                ext = file_path.suffix.lower()
                try:
                    if ext in SUPPORTED_IMAGE_FORMATS:
                        text = self.recognize(file_path, preprocess=preprocess)
                        results[file_path.name] = text
                    elif ext == ".pdf":
                        pages = self.process_pdf(file_path)
                        text = "\n\n".join(pages)
                        results[file_path.name] = text
                    elif ext == ".docx":
                        text = self.process_docx(file_path)
                        results[file_path.name] = text
                    elif ext == ".doc":
                        text = self.process_doc(file_path)
                        results[file_path.name] = text
                    else:
                        logger.warning(f"跳过不支持的文件: {file_path.name}")
                except Exception as e:
                    logger.error(f"处理文件失败 {file_path.name}: {e}")
                    results[file_path.name] = f"[ERROR] {str(e)}"

        logger.info(f"批量处理完成，共处理 {len(results)} 个文件")
        return results