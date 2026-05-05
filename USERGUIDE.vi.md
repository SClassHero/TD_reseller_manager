# Hướng dẫn sử dụng nhanh - TD Reseller Manager

Đây là bản hướng dẫn tiếng Việt ngắn gọn. Bản tiếng Anh `USERGUIDE.md` vẫn là
tài liệu chi tiết nhất.

## 1. Đổi ngôn ngữ giao diện

Trên thanh trên cùng có nút:

```text
English -> Tiếng Việt
```

hoặc:

```text
Tiếng Việt -> English
```

Bạn có thể đổi qua lại bất cứ lúc nào. Việc này chỉ đổi nhãn giao diện. Dữ liệu,
trạng thái đơn hàng, kế toán, tồn kho và database không bị thay đổi.

## 2. Sản phẩm và danh mục

Vào **Categories / Danh mục** để tạo nhóm sản phẩm.

Vào **Products / Sản phẩm** để tạo sản phẩm. Các giá trong sản phẩm là giá tham
khảo:

- **Sale Price / Giá bán**: dùng để điền sẵn khi tạo đơn.
- **Cost Price / Giá vốn**: dùng để điền sẵn khi nhập kho.

Doanh thu và COGS thật được tính từ đơn hàng và lô nhập kho, không tính trực
tiếp từ giá tham khảo này.

## 3. Nhập kho

Vào **Inventory / Tồn kho** rồi chọn **Add Intake / Thêm nhập kho**.

Mỗi lần nhập hàng tạo một lô tồn kho riêng. Khi bán hàng, app trừ hàng theo FIFO:
lô cũ nhất được dùng trước.

Nếu hàng chưa về nhưng đã nhận đặt trước, có thể nhập số lượng âm để đánh dấu
backorder/pre-order.

## 4. Đơn hàng

Vào **Orders / Đơn hàng** rồi chọn **New Order / Tạo đơn hàng**.

Trạng thái chính:

- **Draft / Nháp**: chưa ảnh hưởng tồn kho, doanh thu, COGS.
- **Processing / Đang xử lý**: giữ/trừ tồn kho.
- **Completed / Hoàn tất**: tính vào doanh thu, COGS, công nợ.
- **Cancelled / Đã hủy**: đảo lại tác động tồn kho nếu trước đó đã giữ/trừ.

## 5. Thanh toán và công nợ

Thanh toán được ghi trong chi tiết đơn hàng. Công nợ khách hàng được tính trực
tiếp từ:

```text
đơn đã hoàn tất - thanh toán đã ghi nhận
```

Không cần tự sửa công nợ bằng tay.

## 6. Trả hàng và hoàn tiền

Dùng **Returns / Trả hàng** khi khách gửi hàng về.

Mặc định, app giả định hàng trả còn bán được và sẽ nhập lại vào kho dưới dạng
lô returned-goods mới. Nếu hàng hư hoặc chỉ bán lại được một phần, giảm
**Sellable Qty / SL còn bán được** hoặc bỏ chọn restock.

Dùng **Refunds / Hoàn tiền** để ghi tiền hoàn cho khách. Hoàn tiền làm giảm
doanh thu ròng.

## 7. Hàng hư, mất, không bán được

Trong **Inventory / Tồn kho**, mở chi tiết lô và dùng **Write Off / Ghi hao hụt**.

Tính năng này dùng cho:

- hàng hư
- hàng không bán lại được
- hàng mất
- hàng mẫu/tặng
- điều chỉnh sai lệch kiểm kê

Write-off làm giảm tồn kho và ghi nhận chi phí hao hụt, nhưng không đổi doanh
thu, hoàn tiền, hay COGS của đơn hàng cũ.

## 8. Báo cáo

**Dashboard / Tổng quan** cho thấy doanh thu ròng, chi phí bán hàng, lợi nhuận
gộp, biên lợi nhuận, tồn kho và đơn đang xử lý.

**Reports / Báo cáo** dùng để xem sâu hơn về bán hàng, tồn kho, lợi nhuận và
công nợ khách hàng.

## 9. Sao lưu

Vào **Settings / Cài đặt** để tạo backup thủ công hoặc cấu hình auto-backup.
Backup gồm database và ảnh sản phẩm.

Nên tải backup xuống máy khác hoặc lưu vào nơi an toàn định kỳ.
