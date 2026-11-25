package ru.moyakorzina.app;

import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.DashPathEffect;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.drawable.Drawable;
import android.os.SystemClock;

/**
 * Знак состояния процесса на панели сети: те же семь форм, что на веб-экранах (макрос sg в
 * app/web/templates/phone/_icons.html): сетка 24×24, обводка без заливки, круглые концы.
 * Форма говорит, что происходит, число рядом — сколько, цвет только подтверждает.
 * Кольцо «идёт» вращается — это единственное движение в языке — и стоит, если анимации
 * в системе выключены (spin = false).
 */
final class SignDrawable extends Drawable {
    /** Состояния процесса (DESIGN.md, «Состояния процессов»). NONE место занимает, но не рисует. */
    enum Sign {
        NONE(0, ""), QUEUE(0xFF5F636B, "в очереди"), GOING(0xFF111214, "идёт"),
        DONE(0xFF17703A, "готово"), PART(0xFF8A5A00, "не всё"), ACT(0xFF111214, "нужен ваш шаг"),
        FAIL(0xFFB71C1C, "не вышло"), STALE(0xFF5F636B, "давно");

        final int ink;       // цвет знака
        final String words;  // состояние словами: для чтения с экрана

        Sign(int ink, String words) {
            this.ink = ink;
            this.words = words;
        }
    }

    private static final long TURN_MS = 900;      // оборот кольца, как kz-spin в phone.css
    private static final int TRACK = 0xFFDADDE1;  // --line2: бледный круг под дугой «идёт»

    private final Sign sign;
    private final int size;
    private final boolean spin;
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path path = new Path();
    private final Runnable tick = this::invalidateSelf;

    SignDrawable(Sign sign, int sizePx, boolean spin) {
        this.sign = sign;
        this.size = sizePx;
        this.spin = spin;
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeCap(Paint.Cap.ROUND);
        paint.setStrokeJoin(Paint.Join.ROUND);
        paint.setStrokeWidth(2f);
        if (sign == Sign.QUEUE) paint.setPathEffect(new DashPathEffect(new float[] {3.2f, 3.2f}, 0));
    }

    @Override
    public void draw(Canvas c) {
        if (sign == Sign.NONE) return;
        Rect b = getBounds();
        int saved = c.save();
        c.translate(b.left, b.top);
        c.scale(b.width() / 24f, b.height() / 24f);
        paint.setColor(sign.ink);
        path.reset();
        switch (sign) {
            case QUEUE:
                path.addCircle(12, 12, 8, Path.Direction.CW);
                break;
            case GOING:
                paint.setColor(TRACK);
                c.drawCircle(12, 12, 8, paint);
                paint.setColor(sign.ink);
                // Время общее для всех знаков: новый знак на каждой строке не сбрасывает оборот.
                if (spin) c.rotate(SystemClock.uptimeMillis() % TURN_MS * 360f / TURN_MS, 12, 12);
                path.arcTo(4, 4, 20, 20, -90, 90, true);
                break;
            case DONE:
                poly(5, 12.5f, 9.5f, 17, 19, 7.5f);
                break;
            case PART:
                poly(12, 4, 21, 20, 3, 20);
                path.close();
                poly(12, 10, 12, 14);
                poly(12, 17.5f, 12, 17.51f);
                break;
            case ACT:
                poly(14, 4, 19, 4, 19, 20, 14, 20);
                poly(4, 12, 15, 12);
                poly(11, 8, 15, 12, 11, 16);
                break;
            case FAIL:
                path.addCircle(12, 12, 8.5f, Path.Direction.CW);
                poly(9, 9, 15, 15);
                poly(15, 9, 9, 15);
                break;
            case STALE:
                path.addCircle(12, 12, 8.5f, Path.Direction.CW);
                poly(12, 7.5f, 12, 12, 15, 14);
                break;
            default:
                break;
        }
        c.drawPath(path, paint);
        c.restoreToCount(saved);
        if (sign == Sign.GOING && spin) {
            unscheduleSelf(tick);
            scheduleSelf(tick, SystemClock.uptimeMillis() + 16);
        }
    }

    /** Ломаная по парам x, y. */
    private void poly(float... xy) {
        path.moveTo(xy[0], xy[1]);
        for (int i = 2; i < xy.length; i += 2) path.lineTo(xy[i], xy[i + 1]);
    }

    @Override
    public int getIntrinsicWidth() {
        return size;
    }

    @Override
    public int getIntrinsicHeight() {
        return size;
    }

    @Override
    public void setAlpha(int alpha) {
        paint.setAlpha(alpha);
    }

    @Override
    public void setColorFilter(ColorFilter filter) {
        paint.setColorFilter(filter);
    }

    @Override
    public int getOpacity() {
        return PixelFormat.TRANSLUCENT;
    }
}
