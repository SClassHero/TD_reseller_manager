# TD Reseller Manager

Ứng dụng web tự host để quản lý bán hàng, tồn kho, khách hàng, đơn hàng,
trả hàng, hoàn tiền, công nợ và báo cáo lợi nhuận cho mô hình bán hàng nhỏ.

Ứng dụng dùng Flask + HTMX + SQLite. Dữ liệu tiền lưu bằng VND. Giao diện có
thể đổi giữa English và Tiếng Việt bằng nút trên thanh trên cùng. Việc đổi ngôn
ngữ chỉ thay đổi nhãn hiển thị, không đổi dữ liệu, mã trạng thái, kế toán, hay
cấu trúc database.

## Đăng nhập mặc định

```text
username: admin
password: admin123
```

Sau khi chạy app lần đầu, nên đổi mật khẩu trong **Settings / Cài đặt** và tạo
mã khôi phục mật khẩu.

## Quy trình sử dụng cơ bản

1. Tạo danh mục sản phẩm.
2. Tạo sản phẩm, nhập giá bán tham khảo, giá vốn tham khảo, tồn kho tối thiểu.
3. Nhập hàng trong **Inventory / Tồn kho** bằng **Add Intake / Thêm nhập kho**.
4. Tạo khách hàng.
5. Tạo đơn hàng.
6. Chuyển đơn sang **Processing / Đang xử lý** hoặc **Completed / Hoàn tất** để
   trừ tồn kho theo FIFO.
7. Ghi nhận thanh toán.
8. Nếu có trả hàng, dùng **Returns / Trả hàng**. Nếu hoàn tiền, dùng
   **Refunds / Hoàn tiền**.
9. Xem **Dashboard / Tổng quan** và **Reports / Báo cáo** để kiểm tra doanh thu,
   COGS, lợi nhuận, công nợ và tồn kho.

## Các quy tắc quan trọng

- Doanh thu đến từ giá bán thực tế trong đơn hàng, không phải giá tham khảo của
  sản phẩm.
- COGS dùng FIFO từ các lô nhập kho.
- Trả hàng không sửa lô FIFO gốc. Nếu hàng còn bán được, app tạo một lô
  returned-goods mới.
- Hàng hư, mất, không bán được, hoặc sai lệch kiểm kê nên dùng **Write Off /
  Ghi hao hụt**.
- Công nợ khách hàng được tính trực tiếp từ đơn đã hoàn tất và thanh toán.

## Chạy cục bộ

Cho developer:

```powershell
python app.py
```

Mở:

```text
http://localhost:5000
```

Cho người dùng không rành kỹ thuật trên Windows, developer có thể build bản
portable. Xem `PACKAGING_WINDOWS.md`.

## Tài liệu liên quan

- `USERGUIDE.vi.md`: hướng dẫn tiếng Việt ngắn gọn
- `README.md`: README tiếng Anh
- `USERGUIDE.md`: hướng dẫn tiếng Anh đầy đủ
- `CHANGELOG.md`: lịch sử phiên bản app
- `SCHEMA_CHANGELOG.md`: lịch sử thay đổi database
