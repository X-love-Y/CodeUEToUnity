using UnityEngine;

/// <summary>
/// 由 UE 的 UWallClimbComponent 迁移而来。
///
/// 关键差异: UE 组件由宿主在构造函数里用 CreateDefaultSubobject 创建,
/// 生命周期挂在宿主上; Unity 组件是独立 GameObject 组件,
/// 需要自己处理"宿主存在性"(原来对应 Cast&lt;ACharacter&gt;(GetOwner()))。
/// </summary>
public class WallClimbComponent : MonoBehaviour
{
    [Tooltip("判定可攀爬的最大墙面偏离垂直方向的角度, 对应 UE 的 MaxClimbableAngle")]
    [SerializeField] private float maxClimbableAngle = 45f;

    [Tooltip("检测前方墙面的距离。UE 原值 80cm")]
    [SerializeField] private float traceDistance = 0.8f;

    [Tooltip("攀爬高度上限, 超过后无法继续。UE 原值 400cm")]
    [SerializeField] private float maxClimbHeight = 4f;

    [Tooltip("重新检测墙面的间隔。UE 原值 TickInterval=0.05 + 内部 0.1s 节流")]
    [SerializeField] private float recheckInterval = 0.1f;

    [Tooltip("射线起点。留空则用角色胶囊体中心")]
    [SerializeField] private Transform traceOrigin;

    [Tooltip("可攀爬层。UE 的 ECC_Visibility 通道在 Unity 要换成 LayerMask, 必须在项目里手工建层")]
    [SerializeField] private LayerMask climbableMask = ~0;

    private bool isClimbing;
    private Vector3 surfaceNormal = Vector3.zero;
    private float climbStartY;
    private float traceTimer;

    public bool IsClimbing => isClimbing;
    public Vector3 SurfaceNormal => surfaceNormal;

    private void Update()
    {
        if (!isClimbing)
        {
            return;
        }

        // UE 用 TickInterval 给组件降频; Unity 的 Update 每帧必调,
        // 所以这里用累加计时器复刻同样的节流效果。
        traceTimer += Time.deltaTime;
        if (traceTimer < recheckInterval)
        {
            return;
        }

        traceTimer = 0f;
        if (!IsOnClimbableSurface())
        {
            StopClimb();
        }
    }

    /// <summary>对应 UE UWallClimbComponent::TryStartClimb()</summary>
    public bool TryStartClimb()
    {
        // 对应 UE 的 Cast<ACharacter>(GetOwner()) 空值检查
        if (GetComponent<CharacterController>() == null)
        {
            Debug.LogWarning("WallClimbComponent 必须挂在带 CharacterController 的角色上");
            return false;
        }

        if (!TraceWall(out RaycastHit hit))
        {
            return false;
        }

        // UE 用 Acos(Normal.Z) 算墙面倾角; Unity 是 Y-up, 直接用 Vector3.Angle(Vector3.up, normal)
        // 更直观, 两者对垂直墙面都得到 90 度。
        float surfaceAngle = Vector3.Angle(Vector3.up, hit.normal);
        float deviationFromVertical = Mathf.Abs(surfaceAngle - 90f);
        if (deviationFromVertical > maxClimbableAngle)
        {
            Debug.LogWarning($"墙面太倾斜, 无法攀爬: angle={surfaceAngle:F1}");
            return false;
        }

        surfaceNormal = hit.normal;
        isClimbing = true;
        climbStartY = transform.position.y;
        traceTimer = 0f;
        return true;
    }

    /// <summary>对应 UE UWallClimbComponent::StopClimb()</summary>
    public void StopClimb()
    {
        isClimbing = false;
        surfaceNormal = Vector3.zero;
        climbStartY = 0f;
        traceTimer = 0f;
    }

    /// <summary>对应 UE UWallClimbComponent::IsOnClimbableSurface()</summary>
    public bool IsOnClimbableSurface()
    {
        if (GetComponent<CharacterController>() == null)
        {
            return false;
        }

        // 超过攀爬高度上限后不允许继续
        if (isClimbing && (transform.position.y - climbStartY) > maxClimbHeight)
        {
            return false;
        }

        if (!TraceWall(out RaycastHit hit))
        {
            return false;
        }

        float surfaceAngle = Vector3.Angle(Vector3.up, hit.normal);
        return Mathf.Abs(surfaceAngle - 90f) <= maxClimbableAngle;
    }

    private bool TraceWall(out RaycastHit hit)
    {
        // 对应 UE 的 LineTraceSingleByChannel(Start, End, ECC_Visibility, Params)
        // UE 用了 FCollisionQueryParams.AddIgnoredActor 忽略自己;
        // Unity 没有参数对象, 常规做法是把角色放到单独的 Layer 并排除在 climbableMask 之外。
        Vector3 origin = traceOrigin != null ? traceOrigin.position : transform.position;
        Vector3 direction = transform.forward;

        return Physics.Raycast(
            origin,
            direction,
            out hit,
            traceDistance,
            climbableMask,
            QueryTriggerInteraction.Ignore);
    }
}
